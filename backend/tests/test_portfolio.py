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


def _value_snapshot(
    portfolio: Portfolio,
    *,
    snapshot_date: date = date(2026, 5, 1),
    market_value: Decimal = Decimal("0"),
    cost_basis: Decimal = Decimal("0"),
    liquidation_value: Decimal = Decimal("0"),
    unrealized_gain: Decimal | None = None,
) -> PortfolioValueSnapshot:
    """Create a snapshot with internally-consistent totals. unrealized_gain
    defaults to market_value - cost_basis so the matching CHECK passes, and every
    total is supplied explicitly since the money fields have no model default."""
    if unrealized_gain is None:
        unrealized_gain = market_value - cost_basis
    return PortfolioValueSnapshot.objects.create(
        portfolio=portfolio,
        snapshot_date=snapshot_date,
        market_value=market_value,
        cost_basis=cost_basis,
        liquidation_value=liquidation_value,
        unrealized_gain=unrealized_gain,
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
