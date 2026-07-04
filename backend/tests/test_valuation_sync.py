from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError, connection, connections, transaction
from django.test import RequestFactory
from django.utils import timezone

from apps.cards.models import Card, CardPrinting
from apps.collection.models import CollectionItem, CollectionLot, Condition, Language
from apps.core.enums import Edition
from apps.core.locks import _ADVISORY_LOCK_NAMESPACE, _SYNC_LOCK_IDS
from apps.core.models import SyncKind, SyncRun, SyncStatus
from apps.core.sync_history import record_run
from apps.portfolio.models import Portfolio, PortfolioValueSnapshot
from apps.pricing.models import PriceSnapshot, Provider
from apps.valuation.admin import ValuationRunAdmin
from apps.valuation.engine import ValuationResult
from apps.valuation.models import ValuationRun, ValuationStatus
from apps.valuation.sync import record_valuation_run, run_valuation

postgres_only = pytest.mark.skipif(
    connection.vendor != "postgresql", reason="advisory locks are Postgres-only"
)


def _priced_holding(portfolio: Portfolio) -> None:
    """One Near-Mint 1st-Edition holding of 1 in ``portfolio``, costed and priced as of
    today, so a valuation over it is fully covered (gain computable)."""
    card = Card.objects.create(name="Eternal Favorite")
    printing = CardPrinting.objects.create(
        card=card, set_code="MP25-EN172", set_rarity="Ultra Rare", set_name="Maximum Pride 2025"
    )
    item = CollectionItem.objects.create(
        printing=printing,
        portfolio=portfolio,
        condition=Condition.NEAR_MINT,
        edition=Edition.FIRST_EDITION,
        language=Language.ENGLISH,
    )
    CollectionLot.objects.create(collection_item=item, quantity=1, unit_cost=Decimal("5.00"))
    PriceSnapshot.objects.create(
        printing=printing,
        edition=Edition.FIRST_EDITION,
        source=Provider.TCGCSV,
        snapshot_date=timezone.localdate(),
        market_price=Decimal("10.00"),
    )


def _record_pricing_success() -> SyncRun:
    """Record today's successful TCGCSV pricing run -- valuation's hard dependency."""
    return record_run(
        SyncKind.TCGCSV_PRICING, SyncStatus.SUCCESS, product_count=1, price_row_count=1
    )


# --- run_valuation: dependency + recording ----------------------------------


@pytest.mark.django_db
def test_run_valuation_values_and_records_success() -> None:
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    _priced_holding(portfolio)
    _record_pricing_success()

    result = run_valuation()

    assert result is not None
    assert (result.portfolios_seen, result.snapshots_created, result.holdings_valued) == (1, 1, 1)
    snap = PortfolioValueSnapshot.objects.get(
        portfolio=portfolio, snapshot_date=timezone.localdate()
    )
    assert snap.market_value == Decimal("10.00")  # 1 x 10.00 x 1.00 (NM)

    run = ValuationRun.objects.get(status=ValuationStatus.SUCCESS)
    assert (run.portfolios_seen, run.snapshots_created) == (1, 1)
    assert (run.holdings_valued, run.holdings_unpriced) == (1, 0)
    assert run.error == ""
    assert run.detail["snapshots_created"] == 1  # full ValuationResult asdict, for audit


@pytest.mark.django_db
def test_run_valuation_skips_without_same_day_pricing_run() -> None:
    """The hard dependency: with no successful TCGCSV pricing run for today, valuation
    refuses -- records a SKIPPED ValuationRun and writes no snapshot. Valuing against a
    missing/partial price table would lock in an uncorrectable (unique-per-day,
    delete-blocked) snapshot."""
    Portfolio.objects.create(name="Yubel Deck")  # would be valued if the run proceeded

    result = run_valuation()

    assert result is None
    assert not PortfolioValueSnapshot.objects.exists()
    run = ValuationRun.objects.get()
    assert run.status == ValuationStatus.SKIPPED
    assert "pricing" in run.error
    assert run.portfolios_seen is None  # nothing was valued -> counts stay NULL


@pytest.mark.django_db
def test_run_valuation_skips_when_today_pricing_run_failed() -> None:
    """With only a FAILED pricing run and no success today, there is no guard-passed
    price baseline to value against, so the dependency is unmet and valuation skips
    (contrast the SUCCESS-then-later-FAILED case below, which proceeds)."""
    Portfolio.objects.create(name="Yubel Deck")
    record_run(SyncKind.TCGCSV_PRICING, SyncStatus.FAILED, error="truncated")

    result = run_valuation()

    assert result is None
    assert ValuationRun.objects.get().status == ValuationStatus.SKIPPED


@pytest.mark.django_db
def test_run_valuation_proceeds_despite_a_later_failed_pricing_run() -> None:
    """A pricing run that FAILED *after* a same-day success does NOT block valuation:
    PriceSnapshot is append-only first-write-wins, so within a UTC day the priced set is
    monotonic -- a later (manual) run can only ADD correct rows, never overwrite the
    completed success's table. So the existence check is sound: the table valuation reads
    is always >= the guard-passed complete baseline, and skipping here would discard a
    correct snapshot for no gain. Guards against a future wrong tightening to "latest run
    = SUCCESS" (considered, not adopted)."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    _priced_holding(portfolio)
    _record_pricing_success()  # the 03:00 scheduled run completed fully
    # A later manual/duplicate run that failed partway, recorded after the success.
    record_run(
        SyncKind.TCGCSV_PRICING, SyncStatus.FAILED, error="manual rerun crashed mid-ingest"
    )

    result = run_valuation()

    assert result is not None  # not blocked by the later FAILED run
    assert result.snapshots_created == 1
    assert PortfolioValueSnapshot.objects.filter(portfolio=portfolio).exists()
    assert ValuationRun.objects.filter(status=ValuationStatus.SUCCESS).exists()


@pytest.mark.django_db
def test_run_valuation_skips_when_pricing_success_is_not_today() -> None:
    """Yesterday's success doesn't carry over -- today's prices must be confirmed today
    (created_at__date is the UTC day, matching the snapshot key's day)."""
    Portfolio.objects.create(name="Yubel Deck")
    run = _record_pricing_success()
    # created_at is auto_now_add (set on insert); QuerySet.update bypasses it to backdate.
    SyncRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(days=1))

    result = run_valuation()

    assert result is None
    assert ValuationRun.objects.get().status == ValuationStatus.SKIPPED


@pytest.mark.django_db
def test_run_valuation_dependency_is_pricing_specific() -> None:
    """A successful *metadata* run today is not the dependency -- valuation needs the
    *pricing* run, since metadata seeds printings but doesn't price them."""
    Portfolio.objects.create(name="Yubel Deck")
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=1, printing_count=1)

    result = run_valuation()

    assert result is None
    assert ValuationRun.objects.get().status == ValuationStatus.SKIPPED


@pytest.mark.django_db
def test_run_valuation_failure_records_failed_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the engine raises, the orchestration records FAILED + the error and re-raises;
    the engine's transaction.atomic already rolled back any partial snapshots."""
    Portfolio.objects.create(name="Yubel Deck")
    _record_pricing_success()

    def _boom() -> None:
        raise RuntimeError("engine boom")

    monkeypatch.setattr("apps.valuation.sync.value_all_portfolios", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        run_valuation()

    assert not PortfolioValueSnapshot.objects.exists()
    failed = ValuationRun.objects.get(status=ValuationStatus.FAILED)
    assert "boom" in failed.error
    assert failed.portfolios_seen is None


@pytest.mark.django_db
def test_run_valuation_rolls_back_snapshots_if_success_record_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The snapshot writes and the SUCCESS ValuationRun commit in one transaction, so a
    failure to record the run rolls the snapshots back too -- an append-only,
    delete-blocked snapshot is never orphaned without its audit row, and a retry then
    recomputes cleanly rather than finding an orphan (snapshot/run atomicity). The
    failure is still recorded as a FAILED run."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    _priced_holding(portfolio)  # the engine would write one snapshot...
    _record_pricing_success()

    def _fail_on_success(
        status: ValuationStatus,
        *,
        result: ValuationResult | None = None,
        error: str = "",
    ) -> ValuationRun:
        if status == ValuationStatus.SUCCESS:
            raise RuntimeError("audit insert boom")
        return record_valuation_run(status, result=result, error=error)

    monkeypatch.setattr("apps.valuation.sync.record_valuation_run", _fail_on_success)

    with pytest.raises(RuntimeError, match="audit insert boom"):
        run_valuation()

    # ...but the failed SUCCESS insert shares its transaction, so the snapshot rolls back.
    assert not PortfolioValueSnapshot.objects.exists()
    # The failure is still recorded (the FAILED path delegates to the real recorder,
    # outside the rolled-back transaction).
    run = ValuationRun.objects.get()
    assert run.status == ValuationStatus.FAILED
    assert "audit insert boom" in run.error


@pytest.mark.django_db
def test_run_valuation_skips_when_lock_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """If another valuation holds the advisory lock, this one skips: returns None and
    records *no* ValuationRun (the sibling holding the lock will record). Contrast the
    dependency skip, which *does* record -- a redundant concurrent invocation isn't its
    own history row, but a genuinely-refused run is. Mirrors run_tcgcsv_sync's lock-skip."""
    Portfolio.objects.create(name="Yubel Deck")
    _record_pricing_success()

    @contextmanager
    def _held() -> Iterator[bool]:
        yield False

    monkeypatch.setattr("apps.valuation.sync.valuation_lock", _held)

    result = run_valuation()

    assert result is None
    assert not ValuationRun.objects.exists()
    assert not PortfolioValueSnapshot.objects.exists()


@pytest.mark.django_db
def test_run_valuation_skips_when_pricing_run_in_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with a same-day pricing SUCCESS, if a pricing run is *currently* active
    (holds the pricing lock -- e.g. a manual rerun), valuation skips and records SKIPPED
    rather than valuing a partially-committed price table into the irreversible daily
    snapshot. Unlike the valuation-lock skip, this one *records* -- no other run is
    covering the day."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    _priced_holding(portfolio)
    _record_pricing_success()

    @contextmanager
    def _pricing_busy(_kind: object) -> Iterator[bool]:
        yield False  # the pricing lock is held by an active pricing run

    monkeypatch.setattr("apps.valuation.sync.sync_lock", _pricing_busy)

    result = run_valuation()

    assert result is None
    assert not PortfolioValueSnapshot.objects.exists()  # nothing valued
    run = ValuationRun.objects.get()
    assert run.status == ValuationStatus.SKIPPED
    assert "in progress" in run.error


@postgres_only
@pytest.mark.django_db
def test_run_valuation_skips_while_real_pricing_lock_is_held() -> None:
    """Real cross-connection exclusion (not a monkeypatch): while a *separate* connection
    holds the TCGCSV pricing advisory lock, run_valuation -- which now takes that lock
    after the dependency check -- skips. Proves the coordination is a genuine Postgres
    guarantee, not a no-op (the test_core advisory-lock pattern)."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    _priced_holding(portfolio)
    _record_pricing_success()

    other = connections.create_connection("default")
    try:
        with other.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_lock(%s, %s)",
                [_ADVISORY_LOCK_NAMESPACE, _SYNC_LOCK_IDS[SyncKind.TCGCSV_PRICING]],
            )
        result = run_valuation()

        assert result is None
        assert not PortfolioValueSnapshot.objects.exists()
        assert ValuationRun.objects.get().status == ValuationStatus.SKIPPED
    finally:
        other.close()  # closing the session releases its advisory lock


# --- value_portfolios management command ------------------------------------


@pytest.mark.django_db
def test_command_values_today() -> None:
    """The command runs the guarded orchestration (run_valuation), same as the task."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    _priced_holding(portfolio)
    _record_pricing_success()
    out = StringIO()

    call_command("value_portfolios", stdout=out)

    assert "Valuation complete" in out.getvalue()
    assert PortfolioValueSnapshot.objects.filter(portfolio=portfolio).exists()
    assert ValuationRun.objects.filter(status=ValuationStatus.SUCCESS).exists()


@pytest.mark.django_db
def test_command_reports_skip_without_pricing_run() -> None:
    Portfolio.objects.create(name="Yubel Deck")
    out = StringIO()

    call_command("value_portfolios", stdout=out)

    assert "skipped" in out.getvalue().lower()
    assert not PortfolioValueSnapshot.objects.exists()
    assert ValuationRun.objects.get().status == ValuationStatus.SKIPPED


# --- ValuationRun model + admin (append-only) -------------------------------


@pytest.mark.django_db
def test_valuation_run_status_check_rejects_unknown_value() -> None:
    """status is guarded by a DB CHECK (not just `choices`), so an out-of-vocabulary
    value is rejected on every backend incl. sqlite (the SyncRun enum-CHECK precedent)."""
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ValuationRun.objects.create(status="bogus")


def test_valuation_run_admin_blocks_edit_and_delete_of_existing() -> None:
    """Append-only history: an existing run can be neither edited nor deleted (these
    per-object checks don't depend on the user); delete is blocked model-wide too,
    dropping the bulk delete_selected action."""
    admin_obj = ValuationRunAdmin(ValuationRun, AdminSite())
    request = RequestFactory().get("/")
    existing = ValuationRun()

    assert admin_obj.has_delete_permission(request) is False
    assert admin_obj.has_delete_permission(request, existing) is False
    assert admin_obj.has_change_permission(request, existing) is False


@pytest.mark.django_db
def test_valuation_run_admin_change_permission_defers_to_user() -> None:
    """Edit-locking must not bypass model-level permissions: the obj=None case (which
    gates the changelist) still defers to the user's perms."""
    admin_obj = ValuationRunAdmin(ValuationRun, AdminSite())
    request = RequestFactory().get("/")

    request.user = User.objects.create_user("limited", is_staff=True)
    assert admin_obj.has_change_permission(request) is False

    request.user = User.objects.create_superuser("super", "super@example.com", "x")
    assert admin_obj.has_change_permission(request) is True
