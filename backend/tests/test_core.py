from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.db import IntegrityError, connection, connections, models, transaction
from django.test import RequestFactory

from apps.core.admin import SyncRunAdmin
from apps.core.locks import _ADVISORY_LOCK_NAMESPACE, advisory_lock
from apps.core.models import SyncKind, SyncRun, SyncStatus
from apps.core.sync_history import last_successful_count, record_run, shrink_floor

postgres_only = pytest.mark.skipif(
    connection.vendor != "postgresql", reason="advisory locks are Postgres-only"
)

# --- SyncRun model ----------------------------------------------------------


@pytest.mark.django_db
def test_sync_run_str() -> None:
    run = record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=14000)
    assert "YGOPRODeck metadata" in str(run)
    assert "Success" in str(run)


@pytest.mark.django_db
def test_sync_run_invalid_kind_rejected_by_db() -> None:
    """`choices` is form-layer only; the CHECK rejects an out-of-vocabulary kind."""
    with pytest.raises(IntegrityError), transaction.atomic():
        SyncRun.objects.create(kind="bogus", status=SyncStatus.SUCCESS)


@pytest.mark.django_db
def test_sync_run_invalid_status_rejected_by_db() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        SyncRun.objects.create(kind=SyncKind.TCGCSV_PRICING, status="bogus")


@pytest.mark.django_db
def test_sync_run_ordering_latest_first() -> None:
    older = record_run(SyncKind.TCGCSV_PRICING, SyncStatus.SUCCESS, product_count=1)
    newer = record_run(SyncKind.TCGCSV_PRICING, SyncStatus.SUCCESS, product_count=2)

    # Default ordering is (kind, -created_at, -id); the later insert sorts first even
    # if the two share a created_at, because -id is the deterministic tiebreaker.
    assert list(SyncRun.objects.all()) == [newer, older]


def test_sync_run_latest_index_defined() -> None:
    """Intent check (runs on every backend): the guard's lookup is index-backed."""
    assert any(
        index.fields == ["kind", "status", "-created_at"] for index in SyncRun._meta.indexes
    )


def test_sync_run_enum_checks_defined() -> None:
    names = {
        c.name for c in SyncRun._meta.constraints if isinstance(c, models.CheckConstraint)
    }
    assert {"sync_run_kind_valid", "sync_run_status_valid"} <= names


# --- sync_history.last_successful_count -------------------------------------


@pytest.mark.django_db
def test_last_successful_count_returns_latest_success() -> None:
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=100)
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=200)

    assert last_successful_count(SyncKind.YGOPRODECK_METADATA, "card_count") == 200


@pytest.mark.django_db
def test_last_successful_count_ignores_failed_runs() -> None:
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=100)
    # A later FAILED run carrying a count must NOT become the baseline: a truncated
    # fetch records FAILED, and counting it would poison the floor.
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.FAILED, card_count=3, error="boom")

    assert last_successful_count(SyncKind.YGOPRODECK_METADATA, "card_count") == 100


@pytest.mark.django_db
def test_last_successful_count_none_without_history() -> None:
    assert last_successful_count(SyncKind.YGOPRODECK_METADATA, "card_count") is None


@pytest.mark.django_db
def test_last_successful_count_scoped_by_kind() -> None:
    record_run(SyncKind.TCGCSV_PRICING, SyncStatus.SUCCESS, product_count=500)

    assert last_successful_count(SyncKind.YGOPRODECK_METADATA, "card_count") is None


@pytest.mark.django_db
def test_last_successful_count_skips_rows_missing_the_dimension() -> None:
    """A later run that didn't record a dimension falls through to the last that did."""
    record_run(SyncKind.TCGCSV_PRICING, SyncStatus.SUCCESS, product_count=500, price_row_count=480)
    # A subsequent success that somehow recorded only products leaves price_row_count NULL;
    # the price floor must still resolve to the earlier run's value, not None.
    record_run(SyncKind.TCGCSV_PRICING, SyncStatus.SUCCESS, product_count=510)

    assert last_successful_count(SyncKind.TCGCSV_PRICING, "product_count") == 510
    assert last_successful_count(SyncKind.TCGCSV_PRICING, "price_row_count") == 480


# --- sync_history.shrink_floor ----------------------------------------------


@pytest.mark.django_db
def test_shrink_floor_applies_tolerance() -> None:
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=10000)

    # 10000 * (1 - 0.02) = 9800
    assert shrink_floor(SyncKind.YGOPRODECK_METADATA, "card_count", tolerance=0.02) == 9800


@pytest.mark.django_db
def test_shrink_floor_none_without_history() -> None:
    """First run: no baseline, so the provider's absolute bootstrap floor applies."""
    assert shrink_floor(SyncKind.TCGCSV_PRICING, "product_count", tolerance=0.10) is None


@pytest.mark.django_db
@pytest.mark.parametrize("bad_tolerance", [1.0, 2.0, -0.5, -0.01])
def test_shrink_floor_rejects_out_of_range_tolerance(bad_tolerance: float) -> None:
    """Fail closed on a misconfigured tolerance: >=1 disables the guard (non-positive
    floor), <0 bricks the sync (floor above last-good). The percent-vs-fraction class of
    operator error (=2 for "2%") must raise, not compute a dangerous floor."""
    with pytest.raises(ValueError, match="tolerance"):
        shrink_floor(SyncKind.YGOPRODECK_METADATA, "card_count", tolerance=bad_tolerance)


@pytest.mark.django_db
def test_shrink_floor_accepts_boundary_tolerances() -> None:
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=1000)

    assert shrink_floor(SyncKind.YGOPRODECK_METADATA, "card_count", tolerance=0.0) == 1000
    assert shrink_floor(SyncKind.YGOPRODECK_METADATA, "card_count", tolerance=0.99) == 10


# --- sync_history.record_run ------------------------------------------------


@pytest.mark.django_db
def test_record_run_persists_counts_and_detail() -> None:
    run = record_run(
        SyncKind.TCGCSV_PRICING,
        SyncStatus.SUCCESS,
        product_count=30000,
        price_row_count=28000,
        detail={"ingest": {"snapshots_created": 12}},
    )

    run.refresh_from_db()
    assert run.kind == SyncKind.TCGCSV_PRICING
    assert run.status == SyncStatus.SUCCESS
    assert (run.product_count, run.price_row_count) == (30000, 28000)
    assert run.card_count is None
    assert run.detail == {"ingest": {"snapshots_created": 12}}
    assert run.error == ""


# --- SyncRunAdmin (append-only) ---------------------------------------------


def test_sync_run_admin_blocks_edit_and_delete_of_existing() -> None:
    """Append-only history: an existing run can be neither edited nor deleted (these
    per-object checks don't depend on the user); delete is blocked model-wide too,
    dropping the bulk delete_selected action."""
    admin_obj = SyncRunAdmin(SyncRun, AdminSite())
    request = RequestFactory().get("/")
    existing = SyncRun()

    assert admin_obj.has_delete_permission(request) is False
    assert admin_obj.has_delete_permission(request, existing) is False
    assert admin_obj.has_change_permission(request, existing) is False


@pytest.mark.django_db
def test_sync_run_admin_change_permission_defers_to_user() -> None:
    """Edit-locking must not bypass model-level permissions: the obj=None case (which
    gates the changelist) still defers to the user's perms."""
    admin_obj = SyncRunAdmin(SyncRun, AdminSite())
    request = RequestFactory().get("/")

    request.user = User.objects.create_user("limited", is_staff=True)
    assert admin_obj.has_change_permission(request) is False

    request.user = User.objects.create_superuser("super", "super@example.com", "x")
    assert admin_obj.has_change_permission(request) is True


# --- advisory_lock ----------------------------------------------------------


@pytest.mark.django_db
def test_advisory_lock_acquires_and_releases() -> None:
    """Yields True when free, and releases on exit so it is re-acquirable. On Postgres
    this exercises the real pg_try_advisory_lock / pg_advisory_unlock SQL (catching a
    typo there); on sqlite it is a no-op that yields True (the Postgres-only pattern)."""
    with advisory_lock(987654) as acquired:
        assert acquired is True

    # Released on exit -> a later acquisition of the same id succeeds again.
    with advisory_lock(987654) as acquired:
        assert acquired is True


@postgres_only
@pytest.mark.django_db
def test_advisory_lock_excludes_a_concurrent_connection() -> None:
    """Real mutual exclusion (the point of the lock): while a *separate* connection
    holds the lock, advisory_lock yields False so a concurrent sync skips. Proves it's
    session-scoped exclusion, not a no-op."""
    other = connections.create_connection("default")
    try:
        with other.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s, %s)", [_ADVISORY_LOCK_NAMESPACE, 555])
        with advisory_lock(555) as acquired:
            assert acquired is False  # the other connection holds it
    finally:
        other.close()  # closing the session releases its advisory lock
