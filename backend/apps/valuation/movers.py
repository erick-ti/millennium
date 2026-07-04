from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.cards.models import CardPrinting
from apps.collection.models import CollectionItem
from apps.valuation.engine import _base_price, _latest_price_map

# The lookback windows the "biggest movers" view offers. A closed set (validated
# at the view → 400) rather than free text: each value is an anchor offset, and a
# small fixed menu keeps the query + the UI selector honest. Days, not an enum
# column, so there is nothing to store and no DB CHECK.
WINDOW_DAYS_CHOICES: tuple[int, ...] = (7, 30, 90)
DEFAULT_WINDOW_DAYS = 30

# The server owns ordering (the read-API convention: the client never sorts a
# page locally). Map each allowlisted ``?ordering=`` token to (sort attribute,
# reverse). This is the first ``?ordering=`` in the codebase, so it is hand-rolled
# to match the existing manual param-validation idiom rather than wiring DRF's
# OrderingFilter (which acts on a queryset; movers is a computed Python list).
_ORDERING: dict[str, tuple[str, bool]] = {
    "pct_change": ("pct_change", False),
    "-pct_change": ("pct_change", True),
    "abs_change": ("abs_change", False),
    "-abs_change": ("abs_change", True),
}
ORDERING_CHOICES: tuple[str, ...] = tuple(_ORDERING)
DEFAULT_ORDERING = "-pct_change"

# Below this older-anchor base price, ``pct_change`` is left NULL rather than
# reported. A move off a near-zero base ($0.05 → $0.95) is a real DOLLAR move but a
# meaningless/explosive PERCENT (+1800%), and dividing by a legitimate 0.00 base is
# undefined; the dollar change is still computed and the row still ranks. NULL
# (never a fake 0% / ∞) is the same partial≠zero posture as ``unrealized_gain``.
# A method constant in code (the engine's CONDITION_FACTORS precedent), not a
# setting/env knob.
PCT_CHANGE_PRICE_FLOOR = Decimal("1.00")


@dataclass(frozen=True, slots=True)
class MoverRow:
    """One ``(printing, edition)`` the user owns, with its price move over the
    window. ``pct_change`` is None when the older-anchor base is below
    ``PCT_CHANGE_PRICE_FLOOR`` (see above); the other fields are always present:
    a pair missing either anchor's usable price is excluded entirely (partial ≠
    zero), never emitted with a zeroed anchor."""

    printing_id: int
    card_id: int
    card_name: str
    set_code: str
    set_rarity: str
    variant_label: str | None
    edition: str
    start_price: Decimal
    end_price: Decimal
    abs_change: Decimal
    pct_change: float | None
    start_date: date
    end_date: date


def _owned_pairs() -> set[tuple[int, str]]:
    """The distinct ``(printing_id, edition)`` pairs the user currently HOLDS.

    A single owned printing+edition can span several ``CollectionItem`` rows
    (different condition/language/portfolio, all part of the natural key), so
    group to the pair. ``quantity`` is derived (SUM of child lots; the cost-basis-
    on-lots decision), and every lot is strictly positive
    (the ``collection_lot_quantity_positive`` CHECK), so the group SUM is 0 only
    for an item with NO lots: a holding identity that exists but holds no copies.
    Those are filtered out (``qty > 0``): "cards I own" means currently held, not
    merely catalogued."""
    rows = (
        CollectionItem.objects.values("printing_id", "edition")
        .annotate(qty=Coalesce(Sum("lots__quantity"), 0))
        .filter(qty__gt=0)
    )
    return {(row["printing_id"], row["edition"]) for row in rows}


def compute_collection_movers(*, window_days: int) -> list[MoverRow]:
    """Biggest price movers among the ``(printing, edition)`` pairs the user owns,
    over ``window_days``.

    Two anchors: ``end`` = today (``timezone.localdate()``, the project's UTC day,
    not the OS-local one), ``start`` = today - window. For each
    anchor, the price is the latest *usable* TCGCSV snapshot ON OR BEFORE that day,
    reusing the valuation engine's ``_latest_price_map`` / ``_base_price`` so the
    market→mid→low fallback and the "a newer high-only row doesn't mask an older
    usable price" rule are applied identically here, never a
    raw ``market_price``. A pair without a usable price at BOTH anchors is excluded
    (partial ≠ zero): newly-priced-within-the-window pairs simply don't appear.

    Returned UNORDERED; the view applies the ``?ordering=`` allowlist + pagination."""
    owned = _owned_pairs()
    if not owned:
        return []

    printing_ids = {printing_id for printing_id, _ in owned}
    today = timezone.localdate()
    start_day = today - timedelta(days=window_days)

    end_map = _latest_price_map(on_or_before=today, printing_ids=printing_ids)
    start_map = _latest_price_map(on_or_before=start_day, printing_ids=printing_ids)

    printings = {
        printing.id: printing
        for printing in CardPrinting.objects.filter(id__in=printing_ids).select_related("card")
    }

    rows: list[MoverRow] = []
    for key in owned:
        end_snap = end_map.get(key)
        start_snap = start_map.get(key)
        if end_snap is None or start_snap is None:
            continue
        end_price = _base_price(end_snap)
        start_price = _base_price(start_snap)
        if end_price is None or start_price is None:
            continue
        printing = printings.get(key[0])
        if printing is None:  # defensive, the PROTECT FK keeps this from happening
            continue

        abs_change = end_price - start_price
        pct_change = (
            float(abs_change / start_price) if start_price >= PCT_CHANGE_PRICE_FLOOR else None
        )
        rows.append(
            MoverRow(
                printing_id=key[0],
                card_id=printing.card_id,
                card_name=printing.card.name,
                set_code=printing.set_code,
                set_rarity=printing.set_rarity,
                variant_label=printing.variant_label,
                edition=key[1],
                start_price=start_price,
                end_price=end_price,
                abs_change=abs_change,
                pct_change=pct_change,
                start_date=start_snap.snapshot_date,
                end_date=end_snap.snapshot_date,
            )
        )
    return rows


def order_rows(rows: list[MoverRow], ordering: str) -> list[MoverRow]:
    """Sort ``rows`` by an allowlisted ``ordering`` token. Stable on a
    ``(printing_id, edition)`` base order so equal primary values page
    deterministically (the slice-5 paginator-determinism lesson, for a list).
    Rows whose ``pct_change`` is NULL (sub-floor base) always sort LAST when
    ordering by percent, in either direction, a missing percent is never treated
    as the largest or smallest value."""
    attribute, reverse = _ORDERING[ordering]
    base = sorted(rows, key=lambda row: (row.printing_id, row.edition))
    if attribute == "pct_change":
        present = [row for row in base if row.pct_change is not None]
        absent = [row for row in base if row.pct_change is None]
        present.sort(
            key=lambda row: row.pct_change if row.pct_change is not None else 0.0,
            reverse=reverse,
        )
        return present + absent
    return sorted(base, key=lambda row: row.abs_change, reverse=reverse)
