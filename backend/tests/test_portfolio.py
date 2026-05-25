from datetime import date
from decimal import Decimal

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.db import IntegrityError, models, transaction
from django.db.models import ProtectedError
from django.test import RequestFactory

from apps.portfolio.admin import PortfolioValueSnapshotAdmin
from apps.portfolio.models import Portfolio, PortfolioValueSnapshot


@pytest.mark.django_db
def test_portfolio_name_must_be_unique() -> None:
    """name is unique so a folder resolves to one portfolio. A single-column
    UNIQUE over a non-null column, so this is enforced on sqlite too."""
    Portfolio.objects.create(name="Yubel Deck")

    with pytest.raises(IntegrityError), transaction.atomic():
        Portfolio.objects.create(name="Yubel Deck")


@pytest.mark.django_db
def test_get_or_create_resolves_folder_to_one_portfolio() -> None:
    """The DS-import path (DECISIONS 2026-05-18): Folder Name find-or-creates a
    portfolio by name. A repeat import of the same folder reuses the row."""
    first, created_first = Portfolio.objects.get_or_create(name="Long-term hold")
    second, created_second = Portfolio.objects.get_or_create(name="Long-term hold")

    assert created_first is True
    assert created_second is False
    assert first == second
    assert Portfolio.objects.count() == 1


@pytest.mark.django_db
def test_str_returns_name() -> None:
    assert str(Portfolio.objects.create(name="Trade binder")) == "Trade binder"


def test_name_is_unique() -> None:
    """Intent check that runs on every backend, independent of DB enforcement."""
    assert Portfolio._meta.get_field("name").unique is True


# --- PortfolioValueSnapshot -------------------------------------------------


class _Derive:
    """Sentinel for _value_snapshot: derive unrealized_gain from coverage."""


_DERIVE = _Derive()


def _value_snapshot(
    portfolio: Portfolio,
    *,
    snapshot_date: date = date(2026, 5, 1),
    market_value: Decimal = Decimal("0"),
    cost_basis: Decimal = Decimal("0"),
    liquidation_value: Decimal = Decimal("0"),
    unrealized_gain: Decimal | None | _Derive = _DERIVE,
    total_card_count: int = 1,
    priced_card_count: int = 1,
    costed_card_count: int = 1,
) -> PortfolioValueSnapshot:
    """Create a snapshot with internally-consistent totals + coverage. Coverage
    defaults to fully complete (1/1/1). unrealized_gain defaults (``_DERIVE``) to
    market_value - cost_basis when coverage is complete and to None otherwise, so
    the gain CHECKs pass; pass it explicitly (incl. None) to override. Every field
    is supplied since the model has no defaults."""
    gain: Decimal | None
    if isinstance(unrealized_gain, _Derive):
        complete = (
            priced_card_count >= total_card_count
            and costed_card_count >= total_card_count
        )
        gain = (market_value - cost_basis) if complete else None
    else:
        gain = unrealized_gain
    return PortfolioValueSnapshot.objects.create(
        portfolio=portfolio,
        snapshot_date=snapshot_date,
        market_value=market_value,
        cost_basis=cost_basis,
        liquidation_value=liquidation_value,
        unrealized_gain=gain,
        total_card_count=total_card_count,
        priced_card_count=priced_card_count,
        costed_card_count=costed_card_count,
        valuation_method="tcgcsv_market",
        valuation_version=1,
    )


@pytest.mark.django_db
def test_value_snapshot_unique_per_day() -> None:
    """One valuation per (portfolio, snapshot_date). Both columns non-null, so
    this plain UNIQUE is enforced on sqlite too."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    _value_snapshot(portfolio)

    with pytest.raises(IntegrityError), transaction.atomic():
        _value_snapshot(portfolio)


@pytest.mark.django_db
def test_snapshots_differing_by_date_are_distinct() -> None:
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    _value_snapshot(portfolio, snapshot_date=date(2026, 5, 1))
    _value_snapshot(portfolio, snapshot_date=date(2026, 5, 2))

    assert portfolio.value_snapshots.count() == 2


@pytest.mark.django_db
def test_deleting_portfolio_with_snapshots_is_protected() -> None:
    """portfolio FK is PROTECT — the value timeline isn't cheaply re-derivable, so
    a portfolio delete must not cascade it away."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    _value_snapshot(portfolio)

    with pytest.raises(ProtectedError):
        portfolio.delete()


@pytest.mark.django_db
def test_totals_are_required() -> None:
    """No model default on the money fields — a valuation is a computed event, so a
    writer that omits a total fails closed (NOT NULL) rather than silently storing 0."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")

    with pytest.raises(IntegrityError), transaction.atomic():
        PortfolioValueSnapshot.objects.create(
            portfolio=portfolio,
            snapshot_date=date(2026, 5, 1),
            total_card_count=0,
            priced_card_count=0,
            costed_card_count=0,
            valuation_method="tcgcsv_market",
            valuation_version=1,
        )


@pytest.mark.django_db
def test_negative_total_rejected_by_db() -> None:
    """CHECK market_value >= 0 — a portfolio total can't be negative (a loss lives
    in unrealized_gain, not the totals). gain stays consistent so only the
    market_value CHECK fires."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")

    with pytest.raises(IntegrityError), transaction.atomic():
        _value_snapshot(portfolio, market_value=Decimal("-1.00"))


@pytest.mark.django_db
def test_unrealized_gain_must_match_market_minus_cost() -> None:
    """CHECK unrealized_gain = market_value - cost_basis — a stored gain can't drift
    from the row's own totals."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")

    with pytest.raises(IntegrityError), transaction.atomic():
        _value_snapshot(
            portfolio,
            market_value=Decimal("100.00"),
            cost_basis=Decimal("60.00"),
            unrealized_gain=Decimal("999.00"),  # != 100 - 60
        )


@pytest.mark.django_db
def test_unrealized_gain_may_be_negative() -> None:
    """A holding underwater is a legitimate negative gain (no sign bound), as long
    as it still equals market_value - cost_basis."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    snap = _value_snapshot(
        portfolio, market_value=Decimal("60.00"), cost_basis=Decimal("100.00")
    )

    assert snap.unrealized_gain == Decimal("-40.00")


@pytest.mark.django_db
def test_snapshots_ordered_latest_first() -> None:
    """Default order is deterministic latest-first within a portfolio — (portfolio,
    snapshot_date) is the unique key, so no tiebreaker is needed."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    may = _value_snapshot(portfolio, snapshot_date=date(2026, 5, 1))
    june = _value_snapshot(portfolio, snapshot_date=date(2026, 6, 1))

    assert list(portfolio.value_snapshots.all()) == [june, may]


@pytest.mark.django_db
def test_value_snapshot_str() -> None:
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    snap = _value_snapshot(portfolio, market_value=Decimal("1250.00"))

    assert str(snap) == "Yubel Deck @ 2026-05-01: 1250.00"


def test_value_snapshot_unique_constraint() -> None:
    """Intent check that runs on every backend, independent of DB enforcement."""
    constraint = next(
        c
        for c in PortfolioValueSnapshot._meta.constraints
        if isinstance(c, models.UniqueConstraint)
    )

    assert constraint.fields == ("portfolio", "snapshot_date")


def test_value_snapshot_admin_blocks_edit_and_delete_of_existing() -> None:
    """Append-only: an existing snapshot can be neither edited nor deleted (the
    per-object checks don't depend on the user). Delete is also blocked at the
    model level, which drops the bulk delete_selected action."""
    admin_obj = PortfolioValueSnapshotAdmin(PortfolioValueSnapshot, AdminSite())
    request = RequestFactory().get("/")
    existing = PortfolioValueSnapshot()

    assert admin_obj.has_delete_permission(request) is False
    assert admin_obj.has_delete_permission(request, existing) is False
    assert admin_obj.has_change_permission(request, existing) is False


@pytest.mark.django_db
def test_value_snapshot_admin_change_permission_defers_to_user() -> None:
    """Edit-locking existing rows must NOT bypass Django's model-level permissions:
    has_change_permission(obj=None) gates the changelist, so it still defers to the
    user's perms — a staff user without them is denied, a superuser allowed."""
    admin_obj = PortfolioValueSnapshotAdmin(PortfolioValueSnapshot, AdminSite())
    request = RequestFactory().get("/")

    request.user = User.objects.create_user("limited", is_staff=True)
    assert admin_obj.has_change_permission(request) is False

    request.user = User.objects.create_superuser("super", "super@example.com", "x")
    assert admin_obj.has_change_permission(request) is True


# --- Coverage (DECISIONS 2026-05-25) ----------------------------------------


@pytest.mark.django_db
def test_partial_coverage_leaves_gain_null() -> None:
    """Partial coverage (some cards unpriced) keeps the totals non-null but leaves
    unrealized_gain NULL: market_value and cost_basis then sum different subsets,
    so their difference is not a gain."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    snap = _value_snapshot(
        portfolio,
        market_value=Decimal("100.00"),
        cost_basis=Decimal("60.00"),
        total_card_count=10,
        priced_card_count=8,
        costed_card_count=10,
    )

    assert snap.unrealized_gain is None
    assert snap.market_value == Decimal("100.00")
    assert snap.market_value_complete is False
    assert snap.is_complete is False


@pytest.mark.django_db
def test_gain_set_while_incomplete_rejected() -> None:
    """CHECK gain_iff_complete — a non-null gain on a partially-covered valuation is
    rejected even when it is arithmetically correct, so the engine must leave it NULL
    under partial coverage."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")

    with pytest.raises(IntegrityError), transaction.atomic():
        _value_snapshot(
            portfolio,
            market_value=Decimal("100.00"),
            cost_basis=Decimal("60.00"),
            unrealized_gain=Decimal("40.00"),  # == market - cost, but coverage is partial
            total_card_count=10,
            priced_card_count=8,
            costed_card_count=10,
        )


@pytest.mark.django_db
def test_complete_with_null_gain_rejected() -> None:
    """CHECK gain_iff_complete is bidirectional: a fully-covered snapshot MUST carry
    the gain, so a complete row with NULL unrealized_gain is rejected — otherwise it
    would read as is_complete yet have no P&L (caught in a Codex adversarial review)."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")

    with pytest.raises(IntegrityError), transaction.atomic():
        _value_snapshot(
            portfolio,
            market_value=Decimal("100.00"),
            cost_basis=Decimal("60.00"),
            unrealized_gain=None,  # NULL while fully covered, so rejected
            total_card_count=3,
            priced_card_count=3,
            costed_card_count=3,
        )


@pytest.mark.django_db
def test_priced_count_cannot_exceed_total() -> None:
    """CHECK priced_count_within_total — priced is a subset of total, never more.
    gain is forced NULL so only the count CHECK can fire."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")

    with pytest.raises(IntegrityError), transaction.atomic():
        _value_snapshot(
            portfolio,
            unrealized_gain=None,
            total_card_count=3,
            priced_card_count=5,
            costed_card_count=3,
        )


@pytest.mark.django_db
def test_costed_count_cannot_exceed_total() -> None:
    """CHECK costed_count_within_total — costed is a subset of total, never more."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")

    with pytest.raises(IntegrityError), transaction.atomic():
        _value_snapshot(
            portfolio,
            unrealized_gain=None,
            total_card_count=3,
            priced_card_count=3,
            costed_card_count=5,
        )


@pytest.mark.django_db
def test_empty_portfolio_snapshot_is_complete() -> None:
    """An empty portfolio writes explicit 0 totals with 0 coverage counts — that is
    vacuously complete (nothing is missing), so unrealized_gain is 0, not NULL."""
    portfolio = Portfolio.objects.create(name="Empty")
    snap = _value_snapshot(
        portfolio, total_card_count=0, priced_card_count=0, costed_card_count=0
    )

    assert snap.is_complete is True
    assert snap.unrealized_gain == Decimal("0")


def test_completeness_properties() -> None:
    """market_value_complete / cost_basis_complete / is_complete derive from the
    counts (no DB), so they can't drift from the stored coverage."""
    partial = PortfolioValueSnapshot(
        total_card_count=10, priced_card_count=8, costed_card_count=10
    )
    assert partial.market_value_complete is False
    assert partial.cost_basis_complete is True
    assert partial.is_complete is False

    full = PortfolioValueSnapshot(
        total_card_count=10, priced_card_count=10, costed_card_count=10
    )
    assert full.is_complete is True

    empty = PortfolioValueSnapshot(
        total_card_count=0, priced_card_count=0, costed_card_count=0
    )
    assert empty.is_complete is True  # vacuously, for an empty portfolio


def test_coverage_constraints_present() -> None:
    """Intent check (every backend): the coverage CHECKs exist by name, so the
    nullable-gain rules are DB-enforced regardless of the test engine."""
    names = {c.name for c in PortfolioValueSnapshot._meta.constraints}

    assert "portfolio_value_snapshot_priced_count_within_total" in names
    assert "portfolio_value_snapshot_costed_count_within_total" in names
    assert "portfolio_value_snapshot_gain_iff_complete" in names


def test_admin_coverage_complete_reflects_is_complete() -> None:
    """The admin's boolean coverage column mirrors the model's is_complete."""
    admin_obj = PortfolioValueSnapshotAdmin(PortfolioValueSnapshot, AdminSite())
    complete = PortfolioValueSnapshot(
        total_card_count=2, priced_card_count=2, costed_card_count=2
    )
    partial = PortfolioValueSnapshot(
        total_card_count=2, priced_card_count=1, costed_card_count=2
    )

    assert admin_obj.coverage_complete(complete) is True
    assert admin_obj.coverage_complete(partial) is False
