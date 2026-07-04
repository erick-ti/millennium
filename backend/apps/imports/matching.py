from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.cards.models import Card, CardPrinting, MetadataSource, PrintingAlias
from apps.cards.normalization import normalize_name
from apps.imports.models import MatchConfidence


@dataclass(frozen=True, slots=True)
class MatchResult:
    """The outcome of matching one normalized row to a printing.

    ``printing`` is the resolved ``CardPrinting`` (None when unmatched);
    ``confidence`` is the tier slice 4 writes to ``ImportRow.match_confidence``;
    ``detail`` is a short human-readable note (for the review queue /
    ``error_message``) explaining the outcome. v1 emits EXACT / MEDIUM / UNMATCHED
    only, HIGH / LOW are reserved for a future fuzzy matcher.

    Slice-4 materialization policy: only **EXACT** is auto-materializable; MEDIUM
    (printing found but the card-name sanity check disagreed) and UNMATCHED both
    route to human review, never auto-committed to the collection.
    """

    printing: CardPrinting | None
    confidence: MatchConfidence
    detail: str


def match_row(data: dict[str, Any]) -> MatchResult:
    """Resolve a normalized DS row (slice 2's ``normalized_data``) to a reconciled
    ``CardPrinting``, alias-aware, with a confidence tier.

    Keys on ``(set_code, set_rarity)`` (``set_code`` is the full card number, so it
    is card-specific), consulting a YGOPRODeck ``PrintingAlias`` from the provisional
    rarity *first* (the authoritative record that TCGCSV reconciled that key to a
    canonical printing, in place; the ``cards/sync.py`` alias-first order), then the
    exact printing (variant NULL). The strict tier rule:

        EXACT     = printing found (exact key or alias) AND card name agrees
        MEDIUM    = printing found but not safe to auto-materialize (card name
                    disagrees / is missing, or the key is a known multi-variant placeholder)
        UNMATCHED = no printing found

    ``card_name`` is a confidence *cross-check*, never part of the key: a found
    printing whose catalog card name disagrees is still returned as the best
    candidate (``set_code`` is more authoritative than the free-text name) but flagged
    MEDIUM so slice 4 routes it to review instead of auto-materializing. No printing
    for the key -> UNMATCHED. There is **no** name-based fallback in v1 (a Yu-Gi-Oh
    card name maps to many printings/rarities/arts, so a name hit identifies the card
    *concept*, not the owned printing, and must never set ``matched_printing``); the
    unmatched detail still records whether the name exists in the catalog as a triage
    hint. The DS ``variant_label`` ("alt art") is informational, not a key: YGOPRODeck
    encodes alt-art as a *distinct rarity* (variant NULL), so the lookup uses variant NULL.

    A pure resolver (reads, never writes); slice 4's orchestration applies the result
    onto the ``ImportRow``.
    """
    set_code = _clean(data.get("set_code"))
    set_rarity = _clean(data.get("set_rarity"))
    card_name = _clean(data.get("card_name"))
    if not set_code or not set_rarity:
        # slice 2 left one NULL (missing column or unmapped rarity), can't key a printing.
        return MatchResult(None, MatchConfidence.UNMATCHED, "missing set_code or set_rarity")

    printing, ambiguous = _resolve_printing(set_code, set_rarity)
    if ambiguous:
        return MatchResult(
            None,
            MatchConfidence.UNMATCHED,
            f"ambiguous: multiple printings for ({set_code}, {set_rarity})",
        )
    if printing is None:
        return MatchResult(
            None, MatchConfidence.UNMATCHED, _unmatched_detail(set_code, set_rarity, card_name)
        )

    # A known multi-variant key is an ambiguous placeholder (reconciliation queued several
    # sellable variants for it rather than splitting): keep it as the best candidate but
    # downgrade to MEDIUM/review even if the name agrees, never EXACT/auto-materialize a
    # holding whose variant is unresolved.
    if printing.is_multi_variant:
        return MatchResult(
            printing,
            MatchConfidence.MEDIUM,
            "matched on (set_code, set_rarity) but the key is a known multi-variant; "
            "which variant is unresolved (review)",
        )

    # Printing found by the authoritative key. Cross-check the card name for confidence.
    if not card_name:
        return MatchResult(
            printing,
            MatchConfidence.MEDIUM,
            "matched on (set_code, set_rarity); card name missing, not cross-checked",
        )
    if normalize_name(printing.card.name) == normalize_name(card_name):
        return MatchResult(printing, MatchConfidence.EXACT, "matched on (set_code, set_rarity)")
    return MatchResult(
        printing,
        MatchConfidence.MEDIUM,
        f"matched on (set_code, set_rarity); card name disagrees: "
        f"row {card_name!r} vs catalog {printing.card.name!r}",
    )


def _resolve_printing(set_code: str, set_rarity: str) -> tuple[CardPrinting | None, bool]:
    """Find the printing for ``(set_code, set_rarity)``: a YGOPRODeck ``PrintingAlias``
    from the provisional rarity *first*, then the exact variant-NULL printing.

    Alias-first mirrors the alias-consumption order in ``cards/sync.py`` and is what
    makes the resolution authoritative: an alias records that TCGCSV reconciled this
    provisional key to a canonical printing, so it must win even if a stale provisional
    row still lingers at the same key. Exact-first would return that stale row, and the
    name check could mark it EXACT, which slice 4 auto-materializes, a wrong/unpriced
    printing in the collection. DS rows always carry the provisional rarity the alias
    keys on, so alias-first never mis-resolves a legitimately-canonical row.

    Returns ``(printing, ambiguous)``; ``ambiguous`` is True when more than one distinct
    printing matches and one can't be safely chosen. In practice ``set_code`` is the
    full card number (card-specific), so a match is 0 or 1; the >1 guard is defensive
    (the natural key permits two cards to share a ``(set_code, set_rarity)`` even if real
    data doesn't), routing that to review rather than silently picking one.
    """
    aliases = list(
        PrintingAlias.objects.filter(
            source=MetadataSource.YGOPRODECK, set_code=set_code, set_rarity=set_rarity
        ).select_related("printing", "printing__card")[:2]
    )
    if len({alias.printing_id for alias in aliases}) > 1:
        return None, True
    if aliases:
        return aliases[0].printing, False

    exact = list(
        CardPrinting.objects.filter(
            set_code=set_code, set_rarity=set_rarity, variant_label__isnull=True
        ).select_related("card")[:2]
    )
    if len(exact) > 1:
        return None, True
    if exact:
        return exact[0], False
    return None, False


def _unmatched_detail(set_code: str, set_rarity: str, card_name: str) -> str:
    """Triage note for an unmatched row: why no printing, plus whether the card name
    exists in the catalog at all.

    The name check is a read-only diagnostic, NOT a fallback: it never sets a
    ``matched_printing`` (a Yu-Gi-Oh card name maps to many printings, so it identifies
    the card concept, not the owned printing). It only tells the reviewer whether the
    miss is "unknown card" vs "known card, this printing/rarity not in the catalog".
    """
    if CardPrinting.objects.filter(set_code=set_code).exists():
        reason = f"no printing at rarity {set_rarity!r} for {set_code} (set_code exists at other rarities)"
    else:
        reason = f"no printing for set_code {set_code}"
    if card_name:
        in_catalog = Card.objects.filter(normalized_name=normalize_name(card_name)).exists()
        reason += f"; card name {'is' if in_catalog else 'is not'} in the catalog"
    return reason


def _clean(value: Any) -> str:
    """``normalized_data`` values are JSON (str | None); coerce to a stripped str."""
    if value is None:
        return ""
    return str(value).strip()
