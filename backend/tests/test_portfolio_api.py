from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.portfolio.models import Portfolio, PortfolioValueSnapshot


@pytest.fixture
def client() -> APIClient:
    user = get_user_model().objects.create_user("reader", "r@example.com", "x")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _snapshot(
    portfolio: Portfolio,
    *,
    snapshot_date: date = date(2026, 1, 15),
    market_value: Decimal = Decimal("100.00"),
    cost_basis: Decimal = Decimal("70.00"),
    unrealized_gain: Decimal | None = Decimal("30.00"),
    total: int = 10,
    priced: int = 10,
    costed: int = 10,
) -> PortfolioValueSnapshot:
    """Build a snapshot. Default values are *complete* coverage (gain non-null);
    pass ``unrealized_gain=None`` + a partial count to model the slice-4a
    nullable-gain case."""
    return PortfolioValueSnapshot.objects.create(
        portfolio=portfolio,
        snapshot_date=snapshot_date,
        market_value=market_value,
        liquidation_value=market_value * Decimal("0.80"),
        cost_basis=cost_basis,
        unrealized_gain=unrealized_gain,
        total_card_count=total,
        priced_card_count=priced,
        costed_card_count=costed,
        valuation_method="tcgcsv_market_condition",
        valuation_version=1,
    )


# --- auth -----------------------------------------------------------------------


@pytest.mark.django_db
def test_endpoints_require_authentication() -> None:
    anon = APIClient()
    assert anon.get(reverse("portfolio:portfolio-list")).status_code == 403
    assert anon.get(reverse("portfolio:portfoliovaluesnapshot-list")).status_code == 403


# --- portfolios ---------------------------------------------------------------


@pytest.mark.django_db
def test_portfolio_list_inlines_latest_snapshot(client: APIClient) -> None:
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    _snapshot(portfolio, snapshot_date=date(2026, 1, 14))
    latest = _snapshot(portfolio, snapshot_date=date(2026, 1, 15))

    resp = client.get(reverse("portfolio:portfolio-list"))

    assert resp.status_code == status.HTTP_200_OK
    [row] = resp.data["results"]
    assert row["name"] == "Yubel Deck"
    # Latest = -snapshot_date.first(), Jan 15, not Jan 14.
    assert row["latest_snapshot"]["id"] == latest.id
    assert row["latest_snapshot"]["snapshot_date"] == "2026-01-15"


@pytest.mark.django_db
def test_portfolio_without_snapshot_returns_null_latest(client: APIClient) -> None:
    """A freshly-created portfolio (e.g. from a DS import that ran before the next
    04:00 UTC valuation beat) has no snapshot, so the inline field is NULL, not omitted."""
    Portfolio.objects.create(name="New Deck")

    resp = client.get(reverse("portfolio:portfolio-list"))

    [row] = resp.data["results"]
    assert row["latest_snapshot"] is None


@pytest.mark.django_db
def test_portfolio_detail_carries_latest_snapshot(client: APIClient) -> None:
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    latest = _snapshot(portfolio)

    resp = client.get(reverse("portfolio:portfolio-detail", args=[portfolio.pk]))

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["latest_snapshot"]["id"] == latest.id


# --- value snapshots -----------------------------------------------------------


@pytest.mark.django_db
def test_snapshot_partial_coverage_serializes_null_unrealized_gain(client: APIClient) -> None:
    """Partial coverage means ``unrealized_gain`` is NULL and ``is_complete`` is False.
    Consumers must handle NULL distinctly from 0."""
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    _snapshot(
        portfolio,
        # 10 cards owned, 7 priced, 5 costed: partial on both sides.
        total=10,
        priced=7,
        costed=5,
        unrealized_gain=None,
    )

    resp = client.get(reverse("portfolio:portfoliovaluesnapshot-list"))

    [row] = resp.data["results"]
    assert row["unrealized_gain"] is None
    assert row["market_value_complete"] is False
    assert row["cost_basis_complete"] is False
    assert row["is_complete"] is False
    assert row["total_card_count"] == 10
    assert row["priced_card_count"] == 7
    assert row["costed_card_count"] == 5


@pytest.mark.django_db
def test_snapshot_complete_coverage_serializes_unrealized_gain(client: APIClient) -> None:
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    _snapshot(portfolio, total=5, priced=5, costed=5, unrealized_gain=Decimal("30.00"))

    resp = client.get(reverse("portfolio:portfoliovaluesnapshot-list"))

    [row] = resp.data["results"]
    assert row["unrealized_gain"] == "30.00"
    assert row["is_complete"] is True


@pytest.mark.django_db
def test_snapshot_list_filters_by_portfolio(client: APIClient) -> None:
    yubel = Portfolio.objects.create(name="Yubel Deck")
    longterm = Portfolio.objects.create(name="Long-term hold")
    _snapshot(yubel)
    longterm_snap = _snapshot(longterm)

    resp = client.get(
        reverse("portfolio:portfoliovaluesnapshot-list"), {"portfolio": longterm.pk}
    )

    [row] = resp.data["results"]
    assert row["id"] == longterm_snap.id


@pytest.mark.django_db
def test_snapshot_list_filters_by_date_range(client: APIClient) -> None:
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    jan_10 = _snapshot(portfolio, snapshot_date=date(2026, 1, 10))
    jan_15 = _snapshot(portfolio, snapshot_date=date(2026, 1, 15))
    _snapshot(portfolio, snapshot_date=date(2026, 1, 20))

    resp = client.get(
        reverse("portfolio:portfoliovaluesnapshot-list"),
        {"from": "2026-01-10", "to": "2026-01-15"},
    )

    ids = {row["id"] for row in resp.data["results"]}
    assert ids == {jan_10.id, jan_15.id}


@pytest.mark.django_db
def test_snapshot_rejects_malformed_date(client: APIClient) -> None:
    resp = client.get(reverse("portfolio:portfoliovaluesnapshot-list"), {"from": "yesterday"})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
