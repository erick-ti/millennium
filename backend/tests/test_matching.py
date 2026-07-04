from typing import Any

import pytest

from apps.cards.models import Card, CardPrinting, MetadataSource, PrintingAlias
from apps.imports.matching import match_row
from apps.imports.models import MatchConfidence


def _data(**overrides: Any) -> dict[str, Any]:
    """A clean normalized row (slice 2's shape), overridable per test."""
    base: dict[str, Any] = {
        "portfolio_name": "Yubel Deck",
        "card_name": "Ash Blossom & Joyous Spring",
        "set_code": "L5DD-ENC09",
        "set_rarity": "Common",
        "variant_label": None,
        "edition": "first",
        "condition": "near_mint",
        "language": "en",
        "quantity": 1,
        "unit_cost": "0.68",
        "acquired_at": "2024-01-15",
    }
    base.update(overrides)
    return base


def _printing(
    name: str = "Ash Blossom & Joyous Spring",
    set_code: str = "L5DD-ENC09",
    set_rarity: str = "Common",
    variant_label: str | None = None,
    is_multi_variant: bool = False,
) -> CardPrinting:
    card = Card.objects.create(name=name)
    return CardPrinting.objects.create(
        card=card,
        set_code=set_code,
        set_rarity=set_rarity,
        set_name="Some Set",
        variant_label=variant_label,
        is_multi_variant=is_multi_variant,
    )


# --- matched -------------------------------------------------------------------


@pytest.mark.django_db
def test_exact_match_on_key_with_name_agreement() -> None:
    printing = _printing()

    result = match_row(_data())

    assert result.printing == printing
    assert result.confidence == MatchConfidence.EXACT


@pytest.mark.django_db
def test_alias_resolves_provisional_rarity_to_canonical() -> None:
    """The DS rarity is the *provisional* YGOPRODeck name; TCGCSV may have corrected
    the seeded printing in place and recorded a PrintingAlias. A row carrying the
    provisional rarity must resolve to the canonical printing via the alias."""
    card = Card.objects.create(name="Super Polymerization")
    canonical = CardPrinting.objects.create(
        card=card, set_code="RA03-EN053", set_rarity="Prismatic Ultimate Rare", set_name="RA03"
    )
    PrintingAlias.objects.create(
        source=MetadataSource.YGOPRODECK,
        card=card,
        set_code="RA03-EN053",
        set_rarity="Ultimate Rare",  # the provisional value DS produces
        printing=canonical,
    )

    result = match_row(
        _data(card_name="Super Polymerization", set_code="RA03-EN053", set_rarity="Ultimate Rare")
    )

    assert result.printing == canonical
    assert result.confidence == MatchConfidence.EXACT


@pytest.mark.django_db
def test_alias_wins_over_a_stale_provisional_row_at_the_same_key() -> None:
    """If a stale provisional printing still lingers at a key the alias maps to a
    canonical printing, the alias wins (the cards/sync.py alias-first order). Exact-
    first would wrongly return the stale row and the name check could mark it EXACT,
    which slice 4 auto-materializes."""
    card = Card.objects.create(name="Super Polymerization")
    canonical = CardPrinting.objects.create(
        card=card, set_code="RA03-EN053", set_rarity="Prismatic Ultimate Rare", set_name="RA03"
    )
    stale = CardPrinting.objects.create(
        card=card, set_code="RA03-EN053", set_rarity="Ultimate Rare", set_name="RA03"
    )
    PrintingAlias.objects.create(
        source=MetadataSource.YGOPRODECK,
        card=card,
        set_code="RA03-EN053",
        set_rarity="Ultimate Rare",  # the provisional key, now mapped to the canonical row
        printing=canonical,
    )

    result = match_row(
        _data(card_name="Super Polymerization", set_code="RA03-EN053", set_rarity="Ultimate Rare")
    )

    assert result.printing == canonical  # alias target, not the stale provisional row
    assert result.printing != stale
    assert result.confidence == MatchConfidence.EXACT


@pytest.mark.django_db
def test_variant_label_is_ignored_for_matching() -> None:
    """The DS "alt art" variant_label is informational: YGOPRODeck encodes alt-art as
    a distinct rarity (variant NULL), so a row carrying variant_label still matches the
    variant-NULL printing on (set_code, set_rarity)."""
    printing = _printing(set_rarity="Platinum Secret Rare")

    result = match_row(_data(set_rarity="Platinum Secret Rare", variant_label="alt art"))

    assert result.printing == printing
    assert result.confidence == MatchConfidence.EXACT


@pytest.mark.django_db
def test_name_disagreement_is_medium_but_still_returns_the_candidate() -> None:
    """set_code is more authoritative than the free-text name: a found printing whose
    catalog card name disagrees is returned as the best candidate at MEDIUM (→ review),
    not discarded."""
    printing = _printing(name="Super Polymerization", set_code="RA03-EN053", set_rarity="Secret Rare")

    result = match_row(
        _data(card_name="Super Poly", set_code="RA03-EN053", set_rarity="Secret Rare")
    )

    assert result.printing == printing
    assert result.confidence == MatchConfidence.MEDIUM
    assert "disagrees" in result.detail


@pytest.mark.django_db
def test_missing_card_name_is_medium() -> None:
    """A printing matched by set_code but no name to cross-check can't be EXACT."""
    printing = _printing()

    result = match_row(_data(card_name=None))

    assert result.printing == printing
    assert result.confidence == MatchConfidence.MEDIUM


@pytest.mark.django_db
def test_known_multi_variant_key_is_medium_not_exact() -> None:
    """A generic printing flagged is_multi_variant (reconciliation queued several
    sellable variants for its key rather than splitting) is an ambiguous placeholder:
    keep it as the best candidate but downgrade EXACT→MEDIUM even when the name agrees,
    so slice 4 routes it to review instead of auto-materializing."""
    printing = _printing(is_multi_variant=True)

    result = match_row(_data())  # set_code/rarity match and the name agrees

    assert result.printing == printing  # best candidate kept, not discarded
    assert result.confidence == MatchConfidence.MEDIUM  # not EXACT
    assert "multi-variant" in result.detail


@pytest.mark.django_db
def test_non_multi_variant_key_with_name_agreement_is_exact() -> None:
    """Positive control: the same match with is_multi_variant=False stays EXACT."""
    printing = _printing(is_multi_variant=False)

    result = match_row(_data())

    assert result.printing == printing
    assert result.confidence == MatchConfidence.EXACT


# --- unmatched -----------------------------------------------------------------


@pytest.mark.django_db
def test_unknown_set_code_is_unmatched() -> None:
    result = match_row(_data(set_code="ZZZ-EN999", card_name="Nonexistent Card"))

    assert result.printing is None
    assert result.confidence == MatchConfidence.UNMATCHED
    assert "no printing for set_code ZZZ-EN999" in result.detail
    assert "is not in the catalog" in result.detail


@pytest.mark.django_db
def test_set_code_exists_at_other_rarity_with_name_in_catalog() -> None:
    """The unmatched detail distinguishes "unknown card" from "known card, this
    rarity not catalogued", and records that the card name is present, a triage hint
    that never sets a printing (no name fallback)."""
    _printing(set_rarity="Common")  # same set_code, different rarity than the query

    result = match_row(_data(set_rarity="Ultra Rare"))

    assert result.printing is None
    assert result.confidence == MatchConfidence.UNMATCHED
    assert "exists at other rarities" in result.detail
    assert "card name is in the catalog" in result.detail


@pytest.mark.django_db
def test_missing_rarity_is_unmatched() -> None:
    result = match_row(_data(set_rarity=None))

    assert result.printing is None
    assert result.confidence == MatchConfidence.UNMATCHED
    assert "missing set_code or set_rarity" in result.detail


@pytest.mark.django_db
def test_ambiguous_multiple_printings_is_unmatched() -> None:
    """Two distinct cards sharing one (set_code, set_rarity) (pathological but allowed
    by the per-card natural key) can't be safely resolved, so route to review rather
    than silently picking one."""
    _printing(name="Card One", set_code="DUP-EN001", set_rarity="Rare")
    _printing(name="Card Two", set_code="DUP-EN001", set_rarity="Rare")

    result = match_row(_data(card_name="Card One", set_code="DUP-EN001", set_rarity="Rare"))

    assert result.printing is None
    assert result.confidence == MatchConfidence.UNMATCHED
    assert "ambiguous" in result.detail
