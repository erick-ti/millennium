from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection, connections
from django.utils import timezone

from apps.alerts.evaluation import AlertEvaluationResult
from apps.alerts.models import AlertEvent, AlertRule, AlertRun, AlertStatus, Direction
from apps.alerts.sync import record_alert_run, run_alerts
from apps.alerts.tasks import compute_alerts
from apps.cards.models import Card, CardPrinting
from apps.collection.models import CollectionItem, CollectionLot, Condition, Language
from apps.core.enums import Edition
from apps.core.locks import _ADVISORY_LOCK_NAMESPACE, _ALERTS_LOCK_ID
from apps.core.models import SyncKind, SyncRun, SyncStatus
from apps.core.sync_history import record_run
from apps.portfolio.models import Portfolio
from apps.pricing.models import PriceSnapshot, Provider

postgres_only = pytest.mark.skipif(
    connection.vendor != "postgresql", reason="advisory locks are Postgres-only"
)

TODAY = timezone.localdate()


def _owned_mover(*, set_code: str = "AAA-EN001") -> CardPrinting:
    """One owned printing that moved +20% (10.00 -> 12.00) over 30 days."""
    card = Card.objects.create(name="Ash Blossom")
    printing = CardPrinting.objects.create(
        card=card, set_code=set_code, set_rarity="Common", set_name="set"
    )
    portfolio = Portfolio.objects.get_or_create(name="Yubel Deck")[0]
    item = CollectionItem.objects.create(
        portfolio=portfolio,
        printing=printing,
        condition=Condition.NEAR_MINT,
        edition=Edition.FIRST_EDITION,
        language=Language.ENGLISH,
    )
    CollectionLot.objects.create(collection_item=item, quantity=1, unit_cost=None, acquired_at=None)
    for days_ago, price in ((30, "10.00"), (0, "12.00")):
        PriceSnapshot.objects.create(
            printing=printing,
            edition=Edition.FIRST_EDITION,
            source=Provider.TCGCSV,
            snapshot_date=TODAY - timedelta(days=days_ago),
            market_price=Decimal(price),
        )
    return printing


def _active_rule() -> AlertRule:
    return AlertRule.objects.create(
        name="up 10%", threshold_pct=Decimal("10.00"), window_days=30, direction=Direction.UP
    )


def _record_pricing_success() -> SyncRun:
    return record_run(
        SyncKind.TCGCSV_PRICING, SyncStatus.SUCCESS, product_count=1, price_row_count=1
    )


# --- dependency + recording -----------------------------------------------------


@pytest.mark.django_db
def test_run_alerts_evaluates_and_records_success() -> None:
    printing = _owned_mover()
    _active_rule()
    _record_pricing_success()

    result = run_alerts()

    assert result is not None
    assert (result.rules_evaluated, result.events_created) == (1, 1)
    event = AlertEvent.objects.get()
    assert event.printing_id == printing.id
    assert event.pct_change == Decimal("20.00")

    run = AlertRun.objects.get(status=AlertStatus.SUCCESS)
    assert (run.rules_evaluated, run.events_created) == (1, 1)
    assert run.error == ""
    assert run.detail["events_created"] == 1  # full AlertEvaluationResult asdict, for audit


@pytest.mark.django_db
def test_run_alerts_skips_without_same_day_pricing_run() -> None:
    """No successful TCGCSV pricing run today → refuse, record SKIPPED, write no events."""
    _owned_mover()
    _active_rule()

    result = run_alerts()

    assert result is None
    assert not AlertEvent.objects.exists()
    run = AlertRun.objects.get()
    assert run.status == AlertStatus.SKIPPED
    assert "pricing" in run.error
    assert run.rules_evaluated is None  # nothing evaluated → counts stay NULL


@pytest.mark.django_db
def test_run_alerts_skips_when_today_pricing_failed() -> None:
    _owned_mover()
    _active_rule()
    record_run(SyncKind.TCGCSV_PRICING, SyncStatus.FAILED, error="truncated")

    assert run_alerts() is None
    assert AlertRun.objects.get().status == AlertStatus.SKIPPED


@pytest.mark.django_db
def test_run_alerts_dependency_is_pricing_specific() -> None:
    """A successful *metadata* run today is not the dependency — alerts need *pricing*."""
    _owned_mover()
    _active_rule()
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=1, printing_count=1)

    assert run_alerts() is None
    assert AlertRun.objects.get().status == AlertStatus.SKIPPED


@pytest.mark.django_db
def test_run_alerts_skips_when_pricing_success_is_not_today() -> None:
    _owned_mover()
    _active_rule()
    run = _record_pricing_success()
    # created_at is auto_now_add; QuerySet.update bypasses it to backdate to yesterday.
    SyncRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(days=1))

    assert run_alerts() is None
    assert AlertRun.objects.get().status == AlertStatus.SKIPPED


@pytest.mark.django_db
def test_run_alerts_proceeds_despite_a_later_failed_pricing_run() -> None:
    """A pricing run that FAILED *after* a same-day success does NOT block alerts (the
    same monotonic-price-table reasoning as run_valuation)."""
    _owned_mover()
    _active_rule()
    _record_pricing_success()
    record_run(SyncKind.TCGCSV_PRICING, SyncStatus.FAILED, error="manual rerun crashed")

    result = run_alerts()

    assert result is not None and result.events_created == 1
    assert AlertRun.objects.filter(status=AlertStatus.SUCCESS).exists()


@pytest.mark.django_db
def test_run_alerts_failure_records_failed_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    _owned_mover()
    _active_rule()
    _record_pricing_success()

    def _boom() -> AlertEvaluationResult:
        raise RuntimeError("evaluation boom")

    monkeypatch.setattr("apps.alerts.sync.evaluate_active_rules", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        run_alerts()

    assert not AlertEvent.objects.exists()
    failed = AlertRun.objects.get(status=AlertStatus.FAILED)
    assert "boom" in failed.error
    assert failed.rules_evaluated is None


@pytest.mark.django_db
def test_run_alerts_rolls_back_events_if_success_record_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The event writes and the SUCCESS AlertRun commit in one transaction, so a failure to
    record the run rolls the events back too — an append-only event is never orphaned
    without its audit row. The failure is still recorded as a FAILED run (outside the tx)."""
    _owned_mover()
    _active_rule()
    _record_pricing_success()

    def _fail_on_success(
        status: AlertStatus,
        *,
        result: AlertEvaluationResult | None = None,
        error: str = "",
    ) -> AlertRun:
        if status == AlertStatus.SUCCESS:
            raise RuntimeError("audit insert boom")
        return record_alert_run(status, result=result, error=error)

    monkeypatch.setattr("apps.alerts.sync.record_alert_run", _fail_on_success)

    with pytest.raises(RuntimeError, match="audit insert boom"):
        run_alerts()

    assert not AlertEvent.objects.exists()  # the in-tx events rolled back
    run = AlertRun.objects.get()
    assert run.status == AlertStatus.FAILED
    assert "audit insert boom" in run.error


@pytest.mark.django_db
def test_run_alerts_skips_when_lock_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """If another run holds the advisory lock, this one skips: returns None and records
    *no* AlertRun (the sibling holding the lock records). Contrast the dependency skip,
    which *does* record — a redundant concurrent invocation isn't its own history row."""
    _owned_mover()
    _active_rule()
    _record_pricing_success()

    @contextmanager
    def _held() -> Iterator[bool]:
        yield False

    monkeypatch.setattr("apps.alerts.sync.alerts_lock", _held)

    assert run_alerts() is None
    assert not AlertRun.objects.exists()
    assert not AlertEvent.objects.exists()


@postgres_only
@pytest.mark.django_db
def test_run_alerts_skips_while_real_lock_is_held() -> None:
    """Real cross-connection exclusion (not a monkeypatch): while a separate connection
    holds the alerts advisory lock, run_alerts skips — proving the coordination is a genuine
    Postgres guarantee, not a no-op (the test_valuation_sync real-lock pattern)."""
    _owned_mover()
    _active_rule()
    _record_pricing_success()

    other = connections.create_connection("default")
    try:
        with other.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_lock(%s, %s)", [_ADVISORY_LOCK_NAMESPACE, _ALERTS_LOCK_ID]
            )
        result = run_alerts()

        assert result is None
        assert not AlertRun.objects.exists()
        assert not AlertEvent.objects.exists()
    finally:
        other.close()  # closing the session releases its advisory lock


# --- management command + Celery task parity ------------------------------------


@pytest.mark.django_db
def test_command_runs_alerts() -> None:
    _owned_mover()
    _active_rule()
    _record_pricing_success()
    out = StringIO()

    call_command("run_alerts", stdout=out)

    assert "Alerts complete" in out.getvalue()
    assert AlertEvent.objects.exists()
    assert AlertRun.objects.filter(status=AlertStatus.SUCCESS).exists()


@pytest.mark.django_db
def test_command_reports_skip_without_pricing_run() -> None:
    _owned_mover()
    _active_rule()
    out = StringIO()

    call_command("run_alerts", stdout=out)

    assert "skipped" in out.getvalue().lower()
    assert AlertRun.objects.get().status == AlertStatus.SKIPPED


@pytest.mark.django_db
def test_task_runs_alerts_and_returns_counts() -> None:
    _owned_mover()
    _active_rule()
    _record_pricing_success()

    assert compute_alerts() == {"rules_evaluated": 1, "events_created": 1, "events_existing": 0}


@pytest.mark.django_db
def test_task_reports_skipped_without_pricing_run() -> None:
    _owned_mover()
    _active_rule()

    assert compute_alerts() == {"skipped": True}
