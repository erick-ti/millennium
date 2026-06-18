from __future__ import annotations

import io
import json
from collections.abc import Generator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.alerts.models import AlertRun, AlertStatus
from apps.cards.models import Card, CardPrinting
from apps.collection.models import CollectionItem, CollectionLot, Condition, Language
from apps.core.enums import Edition
from apps.core.models import SyncKind, SyncRun, SyncStatus
from apps.core.sync_history import record_run
from apps.portfolio.models import Portfolio, PortfolioValueSnapshot
from apps.status.models import HostMetricSample
from apps.status.providers.healthchecks import _fetch_raw
from apps.valuation.models import ValuationRun, ValuationStatus

OVERVIEW = "/api/status/overview/"


@pytest.fixture
def client() -> APIClient:
    user = get_user_model().objects.create_user("reader", "r@example.com", "x")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture(autouse=True)
def _clear_status_cache() -> Generator[None]:
    # The external (checks) tier caches in LocMem under test; clear it around each test
    # so a configured result can't leak into a not-configured one (and vice versa).
    cache.clear()
    yield
    cache.clear()


def _stage(body: dict[str, Any], key: str) -> dict[str, Any]:
    return next(stage for stage in body["pipeline"] if stage["key"] == key)


def test_overview_requires_authentication() -> None:
    # The dashboard exposes operational internals — anonymous → 403 (the global
    # IsAuthenticated; DRF session auth has no 401). No django_db: denied before the
    # handler runs, so nothing reads the DB (the test_schema_requires_authentication
    # precedent).
    assert APIClient().get(OVERVIEW).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_overview_shape(client: APIClient) -> None:
    body = client.get(OVERVIEW).json()
    assert set(body) == {"app", "pipeline", "catalog", "valuation", "recent_runs"}
    # The pipeline is the nightly chain, in order.
    assert [s["key"] for s in body["pipeline"]] == [
        "metadata",
        "pricing",
        "valuation",
        "alerts",
    ]
    assert body["app"]["version"] == "unknown"  # no GIT_SHA build-arg under test
    # The settings module's last segment — "test" on sqlite, "test_postgres" in CI.
    assert body["app"]["environment"] == settings.SETTINGS_MODULE.rsplit(".", 1)[-1]
    assert isinstance(body["app"]["uptime_seconds"], int)
    assert body["app"]["server_time"]


@pytest.mark.django_db
def test_pipeline_grey_when_no_runs(client: APIClient) -> None:
    metadata = _stage(client.get(OVERVIEW).json(), "metadata")
    assert metadata["status"] == "grey"
    assert metadata["green_today"] is False
    assert metadata["last_run_at"] is None
    assert metadata["metric_value"] is None


@pytest.mark.django_db
def test_pipeline_green_on_success_today(client: APIClient) -> None:
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=14000)
    metadata = _stage(client.get(OVERVIEW).json(), "metadata")
    assert metadata["status"] == "green"
    assert metadata["green_today"] is True
    assert metadata["metric_value"] == 14000
    assert metadata["metric_label"] == "cards"


@pytest.mark.django_db
def test_pipeline_red_on_failure_today(client: APIClient) -> None:
    record_run(SyncKind.TCGCSV_PRICING, SyncStatus.FAILED, error="truncated")
    pricing = _stage(client.get(OVERVIEW).json(), "pricing")
    assert pricing["status"] == "red"
    assert pricing["green_today"] is False


@pytest.mark.django_db
def test_pipeline_amber_when_stale(client: APIClient) -> None:
    run = record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=10)
    # A successful run, but from a prior UTC day → stale, not green. update() bypasses
    # auto_now_add to backdate created_at.
    SyncRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(days=2))
    metadata = _stage(client.get(OVERVIEW).json(), "metadata")
    assert metadata["status"] == "amber"
    assert metadata["green_today"] is False


@pytest.mark.django_db
def test_pipeline_amber_on_skipped_valuation(client: APIClient) -> None:
    ValuationRun.objects.create(status=ValuationStatus.SKIPPED, error="no same-day pricing")
    valuation = _stage(client.get(OVERVIEW).json(), "valuation")
    assert valuation["status"] == "amber"


@pytest.mark.django_db
def test_pipeline_dependency_edges(client: APIClient) -> None:
    pipeline = {s["key"]: s for s in client.get(OVERVIEW).json()["pipeline"]}
    # valuation + alerts gate on the same-day pricing run — the drawn edges of the flow.
    assert pipeline["valuation"]["depends_on"] == "pricing"
    assert pipeline["alerts"]["depends_on"] == "pricing"
    assert pipeline["metadata"]["depends_on"] is None
    assert pipeline["pricing"]["depends_on"] is None


@pytest.mark.django_db
def test_catalog_counts(client: APIClient) -> None:
    Card.objects.create(name="Dark Magician")
    held = Card.objects.create(name="Blue-Eyes White Dragon")
    printing = CardPrinting.objects.create(
        card=held, set_code="LOB-001", set_rarity="Common", set_name="Legend of Blue Eyes"
    )
    item = CollectionItem.objects.create(
        portfolio=Portfolio.objects.create(name="Main"),
        printing=printing,
        condition=Condition.NEAR_MINT,
        edition=Edition.FIRST_EDITION,
        language=Language.ENGLISH,
    )
    CollectionLot.objects.create(collection_item=item, quantity=2)
    CollectionLot.objects.create(collection_item=item, quantity=3)

    catalog = client.get(OVERVIEW).json()["catalog"]
    assert catalog["cards"] == 2
    assert catalog["printings"] == 1
    # owned_holdings counts distinct held items; owned_copies SUMs lot quantities —
    # two DISTINCT aggregations (one holding, 2 + 3 = five copies).
    assert catalog["owned_holdings"] == 1
    assert catalog["owned_copies"] == 5


@pytest.mark.django_db
def test_valuation_null_safe_when_unvalued(client: APIClient) -> None:
    valuation = client.get(OVERVIEW).json()["valuation"]
    assert valuation["as_of"] is None
    assert valuation["market_value"] is None  # not 0 — never valued
    assert valuation["complete"] is None
    assert valuation["portfolios_valued"] == 0


@pytest.mark.django_db
def test_valuation_complete_snapshot(client: APIClient) -> None:
    portfolio = Portfolio.objects.create(name="Main")
    PortfolioValueSnapshot.objects.create(
        portfolio=portfolio,
        snapshot_date=timezone.localdate(),
        market_value=Decimal("100.00"),
        liquidation_value=Decimal("80.00"),
        cost_basis=Decimal("60.00"),
        unrealized_gain=Decimal("40.00"),
        total_card_count=2,
        priced_card_count=2,
        costed_card_count=2,
        valuation_method="tcgcsv_market",
        valuation_version=1,
    )
    valuation = client.get(OVERVIEW).json()["valuation"]
    assert valuation["as_of"] == timezone.localdate().isoformat()
    assert valuation["market_value"] == "100.00"
    assert valuation["complete"] is True
    assert valuation["portfolios_valued"] == 1


@pytest.mark.django_db
def test_recent_runs_newest_first(client: APIClient) -> None:
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=1)
    record_run(SyncKind.TCGCSV_PRICING, SyncStatus.SUCCESS, price_row_count=5)
    recent = client.get(OVERVIEW).json()["recent_runs"]
    assert len(recent) == 2
    # newest first (the -created_at, -id ordering); pricing was recorded last.
    assert recent[0]["kind"] == "tcgcsv_pricing"
    assert recent[0]["price_row_count"] == 5


@pytest.mark.django_db
def test_pipeline_metric_falls_back_to_last_good(client: APIClient) -> None:
    # A FAILED sync leaves its count NULL; the tile shows the last-good high-water mark
    # (the last_successful_count fallback), not a blank — even though the latest run failed.
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=14000)
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.FAILED, error="truncated")
    metadata = _stage(client.get(OVERVIEW).json(), "metadata")
    assert metadata["status"] == "red"  # latest run today failed
    assert metadata["metric_value"] == 14000  # ...but the metric shows the last good count


@pytest.mark.django_db
def test_pipeline_valuation_and_alerts_metrics(client: APIClient) -> None:
    # The non-SyncRun stages: their metric_field/label wiring + the AlertRun query path.
    ValuationRun.objects.create(status=ValuationStatus.SUCCESS, holdings_valued=12)
    AlertRun.objects.create(status=AlertStatus.SUCCESS, events_created=4)
    body = client.get(OVERVIEW).json()

    valuation = _stage(body, "valuation")
    assert valuation["status"] == "green"
    assert valuation["green_today"] is True
    assert valuation["metric_value"] == 12
    assert valuation["metric_label"] == "holdings valued"

    alerts = _stage(body, "alerts")
    assert alerts["status"] == "green"
    assert alerts["metric_value"] == 4
    assert alerts["metric_label"] == "events"


@pytest.mark.django_db
def test_pipeline_stale_failed_is_amber(client: APIClient) -> None:
    # A prior-day FAILED run is amber (stale — the chain hasn't run today), NOT red:
    # the ran_today gate colors ahead of the success/fail branch.
    run = record_run(SyncKind.TCGCSV_PRICING, SyncStatus.FAILED, error="truncated")
    SyncRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(days=2))
    pricing = _stage(client.get(OVERVIEW).json(), "pricing")
    assert pricing["status"] == "amber"
    assert pricing["green_today"] is False


@pytest.mark.django_db
def test_valuation_partial_coverage_across_portfolios(client: APIClient) -> None:
    # Two portfolios valued the same day, one only partially priced: market_value SUMs
    # both (not just the latest), and complete is False — partial ≠ zero.
    today = timezone.localdate()
    full = Portfolio.objects.create(name="Full")
    partial = Portfolio.objects.create(name="Partial")
    PortfolioValueSnapshot.objects.create(
        portfolio=full,
        snapshot_date=today,
        market_value=Decimal("100.00"),
        liquidation_value=Decimal("80.00"),
        cost_basis=Decimal("60.00"),
        unrealized_gain=Decimal("40.00"),
        total_card_count=2,
        priced_card_count=2,
        costed_card_count=2,
        valuation_method="tcgcsv_market",
        valuation_version=1,
    )
    PortfolioValueSnapshot.objects.create(
        portfolio=partial,
        snapshot_date=today,
        market_value=Decimal("25.00"),
        liquidation_value=Decimal("20.00"),
        cost_basis=Decimal("0.00"),
        # priced < total → partial → unrealized_gain MUST be NULL (gain_iff_complete CHECK).
        unrealized_gain=None,
        total_card_count=4,
        priced_card_count=1,
        costed_card_count=0,
        valuation_method="tcgcsv_market",
        valuation_version=1,
    )
    valuation = client.get(OVERVIEW).json()["valuation"]
    assert valuation["complete"] is False
    assert valuation["market_value"] == "125.00"  # SUM across both portfolios
    assert valuation["portfolios_valued"] == 2


@pytest.mark.django_db
def test_recent_runs_capped_at_14(client: APIClient) -> None:
    for i in range(16):
        record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=i)
    recent = client.get(OVERVIEW).json()["recent_runs"]
    assert len(recent) == 14  # the [:14] cap
    # newest first → the last-recorded (card_count=15) leads; the two oldest are dropped.
    assert recent[0]["card_count"] == 15
    assert all(run["card_count"] >= 2 for run in recent)


# --- Healthchecks tier (/api/status/checks/) ---------------------------------

CHECKS = "/api/status/checks/"


def test_checks_requires_authentication() -> None:
    assert APIClient().get(CHECKS).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_checks_not_configured_without_key(client: APIClient) -> None:
    # No read-API key in test settings → degrades with NO network call.
    body = client.get(CHECKS).json()
    assert body["configured"] is False
    assert body["available"] is False
    assert body["backup"] is None
    assert body["cd"] is None


@override_settings(
    HEALTHCHECKS_READ_API_KEY="k",
    HEALTHCHECKS_BACKUP_SLUG="backup",
    HEALTHCHECKS_CD_SLUG="deploy",
)
@pytest.mark.django_db
def test_checks_maps_backup_and_cd_by_slug(
    client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = [
        {
            "slug": "backup",
            "name": "Millennium backup",
            "status": "up",
            "last_ping": "2026-06-16T06:00:03+00:00",
            "n_pings": 30,
            # A read-only key omits ping_url but ADDS unique_key — both are sensitive;
            # _row's allowlist must drop both.
            "ping_url": "https://hc-ping.com/SECRET-UUID",
            "unique_key": "STABLE-KEY-XYZ",
        },
        {
            "slug": "deploy",
            "name": "Millennium deploy",
            "status": "grace",
            "last_ping": "2026-06-16T23:40:00+00:00",
            "n_pings": 700,
        },
        {"slug": "doppel-backup", "name": "Doppel backup", "status": "down", "n_pings": 1},
    ]
    monkeypatch.setattr("apps.status.providers.healthchecks._fetch_raw", lambda key: raw)

    body = client.get(CHECKS).json()
    assert body["configured"] is True
    assert body["available"] is True
    assert body["backup"]["status"] == "up"
    assert body["backup"]["name"] == "Millennium backup"
    assert body["backup"]["n_pings"] == 30
    assert body["cd"]["status"] == "grace"
    assert body["cd"]["name"] == "Millennium deploy"
    assert body["cd"]["n_pings"] == 700
    # A co-tenant check is NOT surfaced, and NO credential (ping URL / unique_key)
    # ever leaves the backend — _row is an explicit allowlist.
    assert "doppel" not in str(body).lower()
    assert "SECRET" not in str(body)
    assert "STABLE-KEY" not in str(body)
    assert "unique_key" not in str(body)


@override_settings(HEALTHCHECKS_READ_API_KEY="k", HEALTHCHECKS_BACKUP_SLUG="backup")
@pytest.mark.django_db
def test_checks_degrades_on_provider_error(
    client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(key: str) -> list[Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr("apps.status.providers.healthchecks._fetch_raw", _boom)

    body = client.get(CHECKS).json()
    assert body["configured"] is True
    assert body["available"] is False  # degraded gracefully, NOT a 500
    assert body["backup"] is None


@override_settings(HEALTHCHECKS_READ_API_KEY="k", HEALTHCHECKS_BACKUP_SLUG="nope")
@pytest.mark.django_db
def test_checks_null_when_slug_unmatched(
    client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "apps.status.providers.healthchecks._fetch_raw",
        lambda key: [{"slug": "other", "name": "X", "status": "up", "n_pings": 1}],
    )
    body = client.get(CHECKS).json()
    assert body["configured"] is True
    assert body["available"] is True
    assert body["backup"] is None  # the configured slug "nope" matched no check


@override_settings(HEALTHCHECKS_READ_API_KEY="k", HEALTHCHECKS_BACKUP_SLUG="backup")
@pytest.mark.django_db
def test_checks_salvages_a_malformed_n_pings(
    client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An unexpected provider shape (non-numeric / missing n_pings, missing last_ping)
    # must NOT 500 — it salvages to 0 / None and still serves the check (200).
    monkeypatch.setattr(
        "apps.status.providers.healthchecks._fetch_raw",
        lambda key: [{"slug": "backup", "name": "B", "status": "new", "n_pings": "oops"}],
    )
    response = client.get(CHECKS)
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["available"] is True
    assert body["backup"]["n_pings"] == 0
    assert body["backup"]["last_ping_at"] is None
    assert body["backup"]["status"] == "new"


@override_settings(HEALTHCHECKS_READ_API_KEY="k", HEALTHCHECKS_BACKUP_SLUG="backup")
@pytest.mark.django_db
def test_checks_are_cached_across_requests(
    client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def _counting(key: str) -> list[Any]:
        calls["n"] += 1
        return [{"slug": "backup", "name": "B", "status": "up", "n_pings": 1}]

    monkeypatch.setattr("apps.status.providers.healthchecks._fetch_raw", _counting)
    first = client.get(CHECKS).json()
    second = client.get(CHECKS).json()
    assert calls["n"] == 1  # the second request was served from the cache
    assert first == second


@override_settings(
    HEALTHCHECKS_READ_API_KEY="k", HEALTHCHECKS_BACKUP_SLUG="", HEALTHCHECKS_CD_SLUG=""
)
@pytest.mark.django_db
def test_checks_unset_slug_never_matches_an_empty_slug_co_tenant(
    client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A co-tenant check without a manual slug arrives as slug="". With the backup/cd
    # slugs UNSET, the explicit empty-slug guard must NOT surface it.
    monkeypatch.setattr(
        "apps.status.providers.healthchecks._fetch_raw",
        lambda key: [{"slug": "", "name": "Doppel backup", "status": "down", "n_pings": 9}],
    )
    body = client.get(CHECKS).json()
    assert body["backup"] is None
    assert body["cd"] is None
    assert "doppel" not in str(body).lower()


def _stub_httpx_get(monkeypatch: pytest.MonkeyPatch, status_code: int, json: Any) -> None:
    # httpx requires a request on the response for raise_for_status (even on 2xx).
    response = httpx.Response(
        status_code, json=json, request=httpx.Request("GET", "https://x")
    )
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: response)


def test_fetch_raw_returns_empty_on_a_checksless_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_httpx_get(monkeypatch, 200, {})
    assert _fetch_raw("k") == []


def test_fetch_raw_returns_empty_on_a_nonlist_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_httpx_get(monkeypatch, 200, {"checks": None})
    assert _fetch_raw("k") == []


def test_fetch_raw_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_httpx_get(monkeypatch, 404, {})
    with pytest.raises(httpx.HTTPStatusError):
        _fetch_raw("k")


# --- Host-box infra tier (/api/status/infra/) --------------------------------

INFRA = "/api/status/infra/"

_SAMPLE_DEFAULTS: dict[str, Any] = {
    "cpu_percent": 12.5,
    "load_1m": 0.4,
    "mem_used_mb": 1400,
    "mem_total_mb": 7751,
    "disk_used_gb": 21.0,
    "disk_total_gb": 75.0,
    "net_rx_bytes": 1_000_000,
    "net_tx_bytes": 500_000,
}


def _make_sample(**overrides: Any) -> HostMetricSample:
    return HostMetricSample.objects.create(**{**_SAMPLE_DEFAULTS, **overrides})


def _backdate(sample: HostMetricSample, **delta: float) -> None:
    # update() bypasses auto_now_add to set created_at to a known earlier time.
    HostMetricSample.objects.filter(pk=sample.pk).update(
        created_at=timezone.now() - timedelta(**delta)
    )


def test_infra_requires_authentication() -> None:
    assert APIClient().get(INFRA).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_infra_awaiting_when_no_samples(client: APIClient) -> None:
    # No collector has run (dev / pre-deploy) → degraded, all-null, never a 500.
    body = client.get(INFRA).json()
    assert body["available"] is False
    assert body["stale"] is False
    assert body["sampled_at"] is None
    assert body["cpu_percent"] is None
    assert body["cpu_series"] == []


@pytest.mark.django_db
def test_infra_serves_latest_with_series(client: APIClient) -> None:
    # Three samples; the endpoint serves the newest values + the CPU series oldest→newest.
    _make_sample(cpu_percent=10.0)
    _make_sample(cpu_percent=20.0)
    _make_sample(cpu_percent=30.0)  # newest
    body = client.get(INFRA).json()
    assert body["available"] is True
    assert body["stale"] is False
    assert body["cpu_percent"] == 30.0  # the latest sample
    assert body["mem_total_mb"] == 7751
    assert body["cpu_series"] == [10.0, 20.0, 30.0]  # oldest → newest, left-to-right
    assert body["sampled_at"] is not None


@pytest.mark.django_db
def test_infra_net_throughput_from_two_samples(client: APIClient) -> None:
    prev = _make_sample(net_rx_bytes=0, net_tx_bytes=0)
    _make_sample(net_rx_bytes=60_000, net_tx_bytes=7_500)  # +60000 rx / +7500 tx
    _backdate(prev, seconds=60)  # 60s between the two samples
    body = client.get(INFRA).json()
    # 60000 bytes * 8 / 1000 / 60s = 8.0 kbit/s; 7500 * 8 / 1000 / 60 = 1.0
    assert body["net_rx_kbps"] == 8.0
    assert body["net_tx_kbps"] == 1.0


@pytest.mark.django_db
def test_infra_net_null_after_counter_reset(client: APIClient) -> None:
    # A reboot resets the cumulative counter → latest < prev → no fabricated negative rate.
    prev = _make_sample(net_rx_bytes=1_000_000)
    _make_sample(net_rx_bytes=10)  # counter reset
    _backdate(prev, seconds=60)
    assert client.get(INFRA).json()["net_rx_kbps"] is None


@pytest.mark.django_db
def test_infra_stale_when_latest_is_old(client: APIClient) -> None:
    # The only sample is past the freshness window → stale + not available, but the
    # last-known values are still shown (the UI greys them), not nulled.
    old = _make_sample(cpu_percent=42.0)
    _backdate(old, minutes=30)
    body = client.get(INFRA).json()
    assert body["available"] is False
    assert body["stale"] is True
    assert body["cpu_percent"] == 42.0  # last-known, not nulled


# --- record_host_metrics command --------------------------------------------

_PAYLOAD: dict[str, Any] = {
    "cpu_percent": 12.5,
    "load_1m": 0.4,
    "mem_used_mb": 1400,
    "mem_total_mb": 7751,
    "disk_used_gb": 21.0,
    "disk_total_gb": 75.0,
    "net_rx_bytes": 1000,
    "net_tx_bytes": 2000,
}


def _run_command(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    # The command reads sys.stdin (the host pipes JSON in); inject a StringIO for tests.
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    call_command("record_host_metrics")


@pytest.mark.django_db
def test_record_host_metrics_creates_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    _run_command(monkeypatch, _PAYLOAD)
    sample = HostMetricSample.objects.get()
    assert sample.cpu_percent == 12.5
    assert sample.mem_total_mb == 7751
    assert sample.net_tx_bytes == 2000


@pytest.mark.django_db
def test_record_host_metrics_rejects_missing_field(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = {k: v for k, v in _PAYLOAD.items() if k != "disk_used_gb"}
    with pytest.raises(CommandError, match="missing field: disk_used_gb"):
        _run_command(monkeypatch, bad)
    assert HostMetricSample.objects.count() == 0


@pytest.mark.django_db
def test_record_host_metrics_rejects_out_of_range_cpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(CommandError, match="cpu_percent out of range"):
        _run_command(monkeypatch, {**_PAYLOAD, "cpu_percent": 250.0})
    assert HostMetricSample.objects.count() == 0


@pytest.mark.django_db
def test_record_host_metrics_rejects_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(CommandError, match="invalid JSON"):
        _run_command(monkeypatch, "not json at all")
    assert HostMetricSample.objects.count() == 0


@pytest.mark.django_db
def test_record_host_metrics_prunes_old_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = _make_sample()
    _backdate(stale, days=8)  # older than the 7-day retention window
    fresh = _make_sample()
    _run_command(monkeypatch, _PAYLOAD)  # writes a new sample AND prunes
    ids = set(HostMetricSample.objects.values_list("pk", flat=True))
    assert stale.pk not in ids  # pruned
    assert fresh.pk in ids  # kept
    assert HostMetricSample.objects.count() == 2  # fresh + the just-recorded one
