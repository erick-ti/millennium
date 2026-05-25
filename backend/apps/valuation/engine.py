from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone

from apps.collection.models import CollectionItem, Condition
from apps.portfolio.models import Portfolio, PortfolioValueSnapshot
from apps.pricing.models import PriceSnapshot, Provider

# --- Valuation method (DECISIONS 2026-05-25 — condition factors / liquidation) ---
# Recorded on every snapshot so a row stays interpretable after the formula
# changes: bumping any constant below is a method change -> bump VALUATION_VERSION,
# which applies going forward (history is tagged, never re-valued). Hardcoded here,
# not in settings or a DB table — the engine owns the valuation method; no env knob
# and no mutable config sitting under append-only snapshots.
VALUATION_METHOD = "tcgcsv_market_condition"
VALUATION_VERSION = 1

# Multiplier on the product-level (~ Near Mint) TCGCSV price for a holding's
# condition — TCGCSV prices a product, not a graded card. Anchored at Near Mint =
# 1.00; covers every Condition (a test asserts completeness, so adding a condition
# without a factor fails loudly rather than silently mis-valuing).
CONDITION_FACTORS: dict[str, Decimal] = {
    Condition.MINT.value: Decimal("1.00"),
    Condition.NEAR_MINT.value: Decimal("1.00"),
    Condition.EXCELLENT.value: Decimal("0.90"),
    Condition.GOOD.value: Decimal("0.80"),
    Condition.LIGHT_PLAYED.value: Decimal("0.75"),
    Condition.PLAYED.value: Decimal("0.60"),
    Condition.POOR.value: Decimal("0.40"),
}

# Quick-sell estimate = market value x this flat haircut (DECISIONS 2026-05-25).
# A single knob for v1; a per-condition / per-liquidity model can refine it later.
LIQUIDATION_HAIRCUT = Decimal("0.80")

_CENTS = Decimal("0.01")

# A snapshot is usable for valuation iff it carries a base price (market/mid/low — the
# fields _base_price reads). _latest_price_map filters on this so the latest *usable*
# snapshot wins, not merely the latest row: ingestion persists high-only / direct-low-only
# rows (it drops a row only when all five points are null), and picking such a newer row
# would yield no base price and wrongly mark a holding unpriced even when an older usable
# snapshot exists (DECISIONS 2026-05-25, after a Codex adversarial review).
_USABLE_PRICE = (
    Q(market_price__isnull=False) | Q(mid_price__isnull=False) | Q(low_price__isnull=False)
)


@dataclass(frozen=True, slots=True)
class ValuationResult:
    """Per-run counts from a valuation pass."""

    portfolios_seen: int = 0
    snapshots_created: int = 0
    snapshots_existing: int = 0
    holdings_valued: int = 0
    holdings_unpriced: int = 0


def _money(value: Decimal) -> Decimal:
    """Round a money amount to cents (ROUND_HALF_UP — the money convention)."""
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _base_price(snapshot: PriceSnapshot | None) -> Decimal | None:
    """A printing+edition's base price: the first present of market / mid / low on
    its latest usable snapshot (tested with ``is not None``, so a legitimate 0.00 counts
    and isn't treated as missing). high / direct-low are deliberately not used as
    fallbacks (they'd skew the estimate) — and ``_latest_price_map`` only returns rows
    that have one of these three, so None here means there was no usable snapshot."""
    if snapshot is None:
        return None
    for price in (snapshot.market_price, snapshot.mid_price, snapshot.low_price):
        if price is not None:
            return price
    return None


def _latest_price_map(*, on_or_before: date) -> dict[tuple[int, str], PriceSnapshot]:
    """Map ``(printing_id, edition)`` -> its latest *usable* TCGCSV snapshot on or
    before the day (usable = has a market/mid/low base price; see ``_USABLE_PRICE``).
    A correlated subquery picks each group's max ``snapshot_date`` among usable rows
    (the ``(printing, edition, source, date)`` key has one row per date, so no ties)
    and the outer filter keeps that row — backend-portable (no Postgres-only
    ``DISTINCT ON``, which ``make test``'s sqlite can't run) and bounded to one row per
    printing+edition. Filtering to usable rows means a newer high-only / direct-low-only
    snapshot doesn't mask an older usable price. ``on_or_before`` lets a past day be
    valued with the prices that existed then."""
    latest_date = (
        PriceSnapshot.objects.filter(
            _USABLE_PRICE,
            source=Provider.TCGCSV,
            printing_id=OuterRef("printing_id"),
            edition=OuterRef("edition"),
            snapshot_date__lte=on_or_before,
        )
        .order_by("-snapshot_date")
        .values("snapshot_date")[:1]
    )
    latest = PriceSnapshot.objects.filter(
        _USABLE_PRICE,
        source=Provider.TCGCSV,
        snapshot_date__lte=on_or_before,
        snapshot_date=Subquery(latest_date),
    )
    return {(snap.printing_id, snap.edition): snap for snap in latest}


def _value_portfolio(
    portfolio: Portfolio,
    *,
    day: date,
    price_map: dict[tuple[int, str], PriceSnapshot],
) -> tuple[bool, int, int]:
    """Compute and persist one portfolio's snapshot for ``day``. Append-only:
    ``get_or_create`` on ``(portfolio, day)``, so a same-day re-run is a no-op (the
    first capture stands). Returns ``(created, holdings_valued, holdings_unpriced)``."""
    total_cards = priced_cards = costed_cards = 0
    holdings_valued = holdings_unpriced = 0
    market_value = liquidation_value = cost_basis = Decimal("0.00")

    items = CollectionItem.objects.filter(portfolio=portfolio).prefetch_related("lots")
    for item in items:
        holding_qty = costed_qty = 0
        holding_cost = Decimal("0.00")
        for lot in item.lots.all():
            holding_qty += lot.quantity
            if lot.unit_cost is not None:
                # unit_cost is per-card and exact at 2dp, quantity is an int, so the
                # cost is exact — no rounding. A NULL unit_cost is "unknown": excluded
                # from cost_basis and costed_qty, never coerced to 0 (DECISIONS 2026-05-25).
                holding_cost += lot.unit_cost * lot.quantity
                costed_qty += lot.quantity
        total_cards += holding_qty
        costed_cards += costed_qty
        cost_basis += holding_cost

        base = _base_price(price_map.get((item.printing_id, item.edition)))
        if base is None:
            # Unpriced: excluded from market_value / priced_cards (not zeroed), which
            # leaves the snapshot partial and unrealized_gain NULL.
            holdings_unpriced += 1
            continue
        factor = CONDITION_FACTORS[item.condition]
        holding_market = _money(base * factor * holding_qty)
        market_value += holding_market
        liquidation_value += _money(holding_market * LIQUIDATION_HAIRCUT)
        priced_cards += holding_qty
        holdings_valued += 1

    # Coverage is full only when every owned card was both priced and costed; only
    # then do market_value and cost_basis describe the same whole portfolio and the
    # difference is a true gain — otherwise leave it NULL (DECISIONS 2026-05-25).
    complete = priced_cards >= total_cards and costed_cards >= total_cards
    unrealized_gain = (market_value - cost_basis) if complete else None

    _, created = PortfolioValueSnapshot.objects.get_or_create(
        portfolio=portfolio,
        snapshot_date=day,
        defaults={
            "market_value": market_value,
            "liquidation_value": liquidation_value,
            "cost_basis": cost_basis,
            "unrealized_gain": unrealized_gain,
            "total_card_count": total_cards,
            "priced_card_count": priced_cards,
            "costed_card_count": costed_cards,
            "valuation_method": VALUATION_METHOD,
            "valuation_version": VALUATION_VERSION,
        },
    )
    return created, holdings_valued, holdings_unpriced


def value_all_portfolios() -> ValuationResult:
    """Value every portfolio for today and write append-only snapshots.

    Per holding: cost basis = SUM(quantity x unit_cost) over its lots; market value =
    quantity x base_price x condition_factor, where base_price is the latest *usable*
    TCGCSV price for ``(printing, edition)`` (DECISIONS 2026-05-18 — edition is a
    pricing dimension); liquidation = market x haircut. Unknowns are excluded from the
    totals, never zeroed (DECISIONS 2026-05-25): a NULL-cost lot doesn't count toward
    cost_basis / costed_card_count, an unpriced holding doesn't count toward
    market_value / priced_card_count, and unrealized_gain is left NULL unless coverage
    is full on both sides. Append-only and idempotent — a same-day re-run is a no-op.

    The day is ``timezone.localdate()`` (not ``date.today()``) — the project's UTC day,
    not the worker's OS-local one — since snapshot_date is part of the append-only key,
    so an off-by-one near midnight would misbucket the series (DECISIONS 2026-05-24).
    There is deliberately NO date parameter: holdings are taken as current (lots aren't
    filtered by ``acquired_at`` and there is no disposal model), so only valuing *today*
    yields correct history. Backdating is structurally prevented at the API, not just
    discouraged — the date-parameterized ``_value_portfolios_for_day`` is private and
    test-only (DECISIONS 2026-05-25, after a Codex adversarial review).

    Single-writer: there is no production caller in 4b — the advisory lock, run
    recording, and the ``value_portfolios`` command all land in the slice-4c
    orchestration that will wrap this (deferred so nothing persists an unguarded
    valuation before that coordination exists; DECISIONS 2026-05-25 review #3).
    """
    return _value_portfolios_for_day(timezone.localdate())


def _value_portfolios_for_day(day: date) -> ValuationResult:
    """Value every portfolio for ``day`` (all-or-nothing: the whole pass is one
    ``transaction.atomic()``, so a mid-run failure rolls back every snapshot and a retry
    recomputes the day rather than skipping a half-written, unfixable series).

    INTERNAL / test-only — NOT a production entry point. It stamps each snapshot with
    ``day`` while reading *current* holdings, so a past ``day`` would write today's
    holdings into a historical, unrepairable (unique-per-day, delete-blocked) row.
    ``value_all_portfolios`` is the sole production caller and always passes
    ``timezone.localdate()``; this split lets tests drive a controlled day without
    exposing backdating to production (DECISIONS 2026-05-25, after a Codex review).
    """
    price_map = _latest_price_map(on_or_before=day)
    seen = created = existing = valued = unpriced = 0
    with transaction.atomic():
        for portfolio in Portfolio.objects.all():
            seen += 1
            was_created, n_valued, n_unpriced = _value_portfolio(
                portfolio, day=day, price_map=price_map
            )
            if was_created:
                created += 1
            else:
                existing += 1
            valued += n_valued
            unpriced += n_unpriced
    return ValuationResult(
        portfolios_seen=seen,
        snapshots_created=created,
        snapshots_existing=existing,
        holdings_valued=valued,
        holdings_unpriced=unpriced,
    )
