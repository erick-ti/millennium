from datetime import date
from decimal import Decimal
from unittest import mock

import pytest
from django.utils import timezone

from apps.cards.models import Card, CardPrinting
from apps.collection.models import CollectionItem, CollectionLot, Condition, Language
from apps.core.enums import Edition
from apps.portfolio.models import Portfolio, PortfolioValueSnapshot
from apps.pricing.models import PriceSnapshot, Provider
from apps.valuation import engine
from apps.valuation.engine import (
    CONDITION_FACTORS,
    VALUATION_METHOD,
    VALUATION_VERSION,
    value_all_portfolios,
)

DAY = date(2026, 5, 24)


def _printing(set_code: str = "RA03-EN056", set_rarity: str = "Ultra Rare") -> CardPrinting:
    card = Card.objects.create(name=f"Card {set_code} {set_rarity}")
    return CardPrinting.objects.create(
        card=card,
        set_code=set_code,
        set_rarity=set_rarity,
        variant_label=None,
        set_name="Test Set",
    )


def _holding(
    portfolio: Portfolio,
    printing: CardPrinting,
    *,
    condition: str = Condition.NEAR_MINT,
    edition: str = Edition.FIRST_EDITION,
    language: str = Language.ENGLISH,
) -> CollectionItem:
    return CollectionItem.objects.create(
        printing=printing,
        portfolio=portfolio,
        condition=condition,
        edition=edition,
        language=language,
    )


def _lot(item: CollectionItem, *, quantity: int, unit_cost: Decimal | None) -> CollectionLot:
    return CollectionLot.objects.create(
        collection_item=item, quantity=quantity, unit_cost=unit_cost
    )


def _price(
    printing: CardPrinting,
    *,
    edition: str = Edition.FIRST_EDITION,
    market: Decimal | None = None,
    mid: Decimal | None = None,
    low: Decimal | None = None,
    high: Decimal | None = None,
    direct_low: Decimal | None = None,
    snapshot_date: date = DAY,
) -> PriceSnapshot:
    return PriceSnapshot.objects.create(
        printing=printing,
        edition=edition,
        source=Provider.TCGCSV,
        snapshot_date=snapshot_date,
        market_price=market,
        mid_price=mid,
        low_price=low,
        high_price=high,
        direct_low_price=direct_low,
    )


@pytest.mark.django_db
def test_latest_price_map_scopes_to_printing_ids() -> None:
    """The ``printing_ids`` kwarg (added for the Phase 5 movers query) narrows the
    map to those printings; the default (``None``) stays catalog-wide, the
    contract ``value_all_portfolios`` relies on. Pinned directly because the
    movers API tests can't catch a no-op/over-narrow regression here: their
    owned-only result loop reads the same values whether or not the catalog map is
    scoped."""
    first = Edition.FIRST_EDITION.value
    p1 = _printing(set_code="AAA-EN001")
    p2 = _printing(set_code="BBB-EN001")
    _price(p1, market=Decimal("10.00"))
    _price(p2, market=Decimal("20.00"))

    catalog = engine._latest_price_map(on_or_before=DAY)
    assert set(catalog) == {(p1.id, first), (p2.id, first)}

    scoped = engine._latest_price_map(on_or_before=DAY, printing_ids={p1.id})
    assert set(scoped) == {(p1.id, first)}


@pytest.mark.django_db
def test_values_a_fully_covered_portfolio() -> None:
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    printing = _printing()
    item = _holding(portfolio, printing)  # Near Mint, 1st Edition
    _lot(item, quantity=3, unit_cost=Decimal("5.00"))  # cost basis 15.00
    _price(printing, market=Decimal("10.00"))

    result = engine._value_portfolios_for_day(DAY)

    assert result.portfolios_seen == 1
    assert result.snapshots_created == 1
    assert result.holdings_valued == 1
    snap = PortfolioValueSnapshot.objects.get(portfolio=portfolio, snapshot_date=DAY)
    assert snap.market_value == Decimal("30.00")  # 3 x 10.00 x 1.00 (NM)
    assert snap.liquidation_value == Decimal("24.00")  # 30.00 x 0.80
    assert snap.cost_basis == Decimal("15.00")
    assert snap.unrealized_gain == Decimal("15.00")  # complete -> market - cost
    assert snap.total_card_count == 3
    assert snap.priced_card_count == 3
    assert snap.costed_card_count == 3
    assert snap.is_complete is True
    assert snap.valuation_method == VALUATION_METHOD
    assert snap.valuation_version == VALUATION_VERSION


@pytest.mark.django_db
def test_condition_factor_discounts_market_value() -> None:
    portfolio = Portfolio.objects.create(name="Played binder")
    printing = _printing()
    item = _holding(portfolio, printing, condition=Condition.PLAYED)  # factor 0.60
    _lot(item, quantity=1, unit_cost=Decimal("20.00"))
    _price(printing, market=Decimal("100.00"))

    engine._value_portfolios_for_day(DAY)

    snap = PortfolioValueSnapshot.objects.get(portfolio=portfolio)
    assert snap.market_value == Decimal("60.00")  # 1 x 100.00 x 0.60
    assert snap.liquidation_value == Decimal("48.00")  # 60.00 x 0.80
    assert snap.unrealized_gain == Decimal("40.00")  # 60.00 - 20.00


@pytest.mark.django_db
def test_unpriced_holding_excluded_and_marks_partial() -> None:
    portfolio = Portfolio.objects.create(name="Mixed")
    printing = _printing()
    item = _holding(portfolio, printing)
    _lot(item, quantity=2, unit_cost=Decimal("5.00"))
    # no PriceSnapshot for this printing -> unpriced

    result = engine._value_portfolios_for_day(DAY)

    assert result.holdings_unpriced == 1
    snap = PortfolioValueSnapshot.objects.get(portfolio=portfolio)
    assert snap.market_value == Decimal("0.00")  # unpriced holding excluded, not zeroed-in
    assert snap.cost_basis == Decimal("10.00")  # cost is still known
    assert snap.total_card_count == 2
    assert snap.priced_card_count == 0
    assert snap.costed_card_count == 2
    assert snap.market_value_complete is False
    assert snap.is_complete is False
    assert snap.unrealized_gain is None  # partial -> not computable


@pytest.mark.django_db
def test_unknown_cost_lot_excluded_and_marks_partial() -> None:
    portfolio = Portfolio.objects.create(name="Gifts")
    printing = _printing()
    item = _holding(portfolio, printing)
    _lot(item, quantity=2, unit_cost=None)  # unknown cost (a pull / gift)
    _price(printing, market=Decimal("10.00"))

    engine._value_portfolios_for_day(DAY)

    snap = PortfolioValueSnapshot.objects.get(portfolio=portfolio)
    assert snap.market_value == Decimal("20.00")  # priced
    assert snap.cost_basis == Decimal("0.00")  # no known cost, not coerced upward
    assert snap.priced_card_count == 2
    assert snap.costed_card_count == 0
    assert snap.cost_basis_complete is False
    assert snap.unrealized_gain is None  # cost side incomplete


@pytest.mark.django_db
def test_base_price_falls_back_market_then_mid_then_low() -> None:
    portfolio = Portfolio.objects.create(name="Fallback")
    printing = _printing()
    item = _holding(portfolio, printing)
    _lot(item, quantity=1, unit_cost=Decimal("1.00"))
    _price(printing, market=None, mid=None, low=Decimal("8.00"))  # only low present

    engine._value_portfolios_for_day(DAY)

    snap = PortfolioValueSnapshot.objects.get(portfolio=portfolio)
    assert snap.market_value == Decimal("8.00")  # falls back to low_price
    assert snap.priced_card_count == 1


@pytest.mark.django_db
def test_zero_market_price_is_priced_not_skipped() -> None:
    """A 0.00 price point is a real price (``is not None``), so the holding is priced
    (counts toward priced_card_count) and contributes 0, not treated as unpriced (the
    bug a truthiness ``or`` chain would introduce)."""
    portfolio = Portfolio.objects.create(name="Zero")
    printing = _printing()
    item = _holding(portfolio, printing)
    _lot(item, quantity=1, unit_cost=Decimal("0.00"))
    _price(printing, market=Decimal("0.00"))

    engine._value_portfolios_for_day(DAY)

    snap = PortfolioValueSnapshot.objects.get(portfolio=portfolio)
    assert snap.market_value == Decimal("0.00")
    assert snap.priced_card_count == 1
    assert snap.is_complete is True
    assert snap.unrealized_gain == Decimal("0.00")


@pytest.mark.django_db
def test_uses_latest_price_on_or_before_day() -> None:
    portfolio = Portfolio.objects.create(name="Timeline")
    printing = _printing()
    item = _holding(portfolio, printing)
    _lot(item, quantity=1, unit_cost=Decimal("1.00"))
    _price(printing, market=Decimal("5.00"), snapshot_date=date(2026, 5, 20))
    _price(printing, market=Decimal("10.00"), snapshot_date=date(2026, 5, 24))

    engine._value_portfolios_for_day(date(2026, 5, 24))

    snap = PortfolioValueSnapshot.objects.get(portfolio=portfolio, snapshot_date=date(2026, 5, 24))
    assert snap.market_value == Decimal("10.00")  # the latest snapshot


@pytest.mark.django_db
def test_ignores_price_snapshots_after_the_valuation_day() -> None:
    """The price map only uses snapshots on or before the valuation day, so a
    future-dated snapshot is never picked up. NB only *pricing* is as-of-date,
    holdings are always current, so the command exposes no past-date backfill (see the
    engine docstring); this exercises the price-date filter, not holdings history."""
    portfolio = Portfolio.objects.create(name="Asof")
    printing = _printing()
    item = _holding(portfolio, printing)
    _lot(item, quantity=1, unit_cost=Decimal("1.00"))
    _price(printing, market=Decimal("5.00"), snapshot_date=date(2026, 5, 20))
    _price(printing, market=Decimal("10.00"), snapshot_date=date(2026, 5, 24))

    engine._value_portfolios_for_day(date(2026, 5, 20))

    snap = PortfolioValueSnapshot.objects.get(portfolio=portfolio, snapshot_date=date(2026, 5, 20))
    assert snap.market_value == Decimal("5.00")  # the 5/24 snapshot is in the future


@pytest.mark.django_db
def test_idempotent_same_day_rerun_is_noop() -> None:
    portfolio = Portfolio.objects.create(name="Idem")
    printing = _printing()
    item = _holding(portfolio, printing)
    _lot(item, quantity=1, unit_cost=Decimal("1.00"))
    _price(printing, market=Decimal("10.00"))

    first = engine._value_portfolios_for_day(DAY)
    second = engine._value_portfolios_for_day(DAY)

    assert first.snapshots_created == 1
    assert second.snapshots_created == 0
    assert second.snapshots_existing == 1
    assert PortfolioValueSnapshot.objects.filter(portfolio=portfolio).count() == 1


@pytest.mark.django_db
def test_empty_portfolio_values_to_zero_and_complete() -> None:
    portfolio = Portfolio.objects.create(name="Empty")

    result = engine._value_portfolios_for_day(DAY)

    assert result.snapshots_created == 1
    snap = PortfolioValueSnapshot.objects.get(portfolio=portfolio)
    assert snap.market_value == Decimal("0.00")
    assert snap.cost_basis == Decimal("0.00")
    assert snap.total_card_count == 0
    assert snap.is_complete is True  # vacuously true: nothing is missing
    assert snap.unrealized_gain == Decimal("0.00")


@pytest.mark.django_db
def test_values_each_portfolio_independently() -> None:
    p1 = Portfolio.objects.create(name="One")
    p2 = Portfolio.objects.create(name="Two (empty)")
    printing = _printing()
    item = _holding(p1, printing)
    _lot(item, quantity=1, unit_cost=Decimal("1.00"))
    _price(printing, market=Decimal("10.00"))

    result = engine._value_portfolios_for_day(DAY)

    assert result.portfolios_seen == 2
    assert result.snapshots_created == 2
    assert PortfolioValueSnapshot.objects.get(portfolio=p1).market_value == Decimal("10.00")
    assert PortfolioValueSnapshot.objects.get(portfolio=p2).market_value == Decimal("0.00")


def test_condition_factors_cover_all_conditions() -> None:
    """Every Condition has a factor. An unmapped one would KeyError mid-run, so this
    guards adding a Condition without a multiplier (no DB needed)."""
    assert set(CONDITION_FACTORS) == set(Condition.values)


@pytest.mark.django_db
def test_failed_run_rolls_back_all_snapshots() -> None:
    """A run is all-or-nothing (transaction.atomic): if a later portfolio raises, the
    snapshots already written for earlier portfolios roll back, so a retry recomputes
    the whole day rather than skipping a half-written, unfixable append-only series."""
    Portfolio.objects.create(name="One")
    Portfolio.objects.create(name="Two")

    original = engine._value_portfolio
    calls = {"n": 0}

    def flaky(
        portfolio: Portfolio,
        *,
        day: date,
        price_map: dict[tuple[int, str], PriceSnapshot],
    ) -> tuple[bool, int, int]:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom on the second portfolio")
        return original(portfolio, day=day, price_map=price_map)

    with mock.patch.object(engine, "_value_portfolio", side_effect=flaky):
        with pytest.raises(RuntimeError):
            engine._value_portfolios_for_day(DAY)

    # The first portfolio's snapshot was written then rolled back with the run.
    assert PortfolioValueSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_value_all_portfolios_stamps_today_only() -> None:
    """The public entry has no date parameter, it always stamps timezone.localdate(),
    so a backdated row can't be produced through it (the backdating path is closed at
    the API, not just the CLI)."""
    portfolio = Portfolio.objects.create(name="Today")
    today = timezone.localdate()

    value_all_portfolios()

    snap = PortfolioValueSnapshot.objects.get(portfolio=portfolio)
    assert snap.snapshot_date == today


@pytest.mark.django_db
def test_latest_usable_price_used_when_newer_snapshot_is_unusable() -> None:
    """_latest_price_map picks the latest *usable* snapshot (market/mid/low), so a newer
    high/direct-low-only row doesn't mask an older usable price and wrongly mark the
    holding unpriced."""
    portfolio = Portfolio.objects.create(name="Masking")
    printing = _printing()
    item = _holding(portfolio, printing)
    _lot(item, quantity=1, unit_cost=Decimal("1.00"))
    _price(printing, market=Decimal("7.00"), snapshot_date=date(2026, 5, 20))  # older, usable
    _price(printing, direct_low=Decimal("9.00"), snapshot_date=date(2026, 5, 24))  # newer, unusable

    engine._value_portfolios_for_day(date(2026, 5, 24))

    snap = PortfolioValueSnapshot.objects.get(portfolio=portfolio, snapshot_date=date(2026, 5, 24))
    assert snap.market_value == Decimal("7.00")  # older usable market price, not unpriced
    assert snap.priced_card_count == 1
    assert snap.is_complete is True
