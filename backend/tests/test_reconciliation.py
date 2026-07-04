from __future__ import annotations

import pytest

from apps.cards.models import Card, CardPrinting, MetadataSource, PrintingAlias
from apps.pricing.models import (
    ExternalPriceId,
    Provider,
    UnmatchedProduct,
    UnmatchedReason,
    UnmatchedStatus,
)
from apps.pricing.providers.base import ProductListing
from apps.pricing.reconciliation import reconcile_products_to_printings


def _printing(card: Card, set_code: str, set_rarity: str) -> CardPrinting:
    return CardPrinting.objects.create(
        card=card, set_code=set_code, set_rarity=set_rarity, set_name="Quarter Century Stampede"
    )


def _listing(
    external_id: str, set_code: str, set_rarity: str, name: str = "A Card"
) -> ProductListing:
    return ProductListing(
        external_id=external_id,
        set_code=set_code,
        set_rarity=set_rarity,
        name=name,
        set_name="Quarter Century Stampede",
    )


@pytest.mark.django_db
def test_exact_match_attaches_external_id() -> None:
    card = Card.objects.create(name="Eternal Favorite")
    printing = _printing(card, "MP25-EN172", "Ultra Rare")

    result = reconcile_products_to_printings([_listing("651572", "MP25-EN172", "Ultra Rare")])

    assert result.exact_matched == 1
    assert result.external_ids_created == 1
    assert ExternalPriceId.objects.get(provider=Provider.TCGCSV, external_id="651572").printing == (
        printing
    )
    assert PrintingAlias.objects.count() == 0  # no rarity change → no alias
    assert UnmatchedProduct.objects.count() == 0


@pytest.mark.django_db
def test_prismatic_fallback_corrects_rarity_in_place_and_aliases() -> None:
    card = Card.objects.create(name="Super Polymerization")
    printing = _printing(card, "RA03-EN053", "Ultimate Rare")

    result = reconcile_products_to_printings(
        [_listing("592540", "RA03-EN053", "Prismatic Ultimate Rare", "Super Polymerization (PUR)")]
    )

    assert result.rarity_reconciled == 1
    assert result.aliases_created == 1
    printing.refresh_from_db()
    assert printing.set_rarity == "Prismatic Ultimate Rare"  # corrected in place, same id
    alias = PrintingAlias.objects.get()
    assert (alias.source, alias.card_id, alias.set_code, alias.set_rarity) == (
        MetadataSource.YGOPRODECK,
        card.id,
        "RA03-EN053",
        "Ultimate Rare",  # the provisional key YGOPRODeck will re-emit
    )
    assert alias.printing == printing
    assert ExternalPriceId.objects.filter(external_id="592540", printing=printing).exists()


@pytest.mark.django_db
def test_no_printing_match_is_queued() -> None:
    # No card/printing for this set_code at all (e.g. a Token absent from YGOPRODeck).
    result = reconcile_products_to_printings(
        [_listing("123524", "LDK2-ENT01", "Common", "Kuriboh Token")]
    )

    assert result.queued_no_printing_match == 1
    entry = UnmatchedProduct.objects.get()
    assert entry.reason == UnmatchedReason.NO_PRINTING_MATCH
    assert entry.status == UnmatchedStatus.UNRESOLVED
    assert entry.external_id == "123524"
    assert ExternalPriceId.objects.count() == 0


@pytest.mark.django_db
def test_rarity_disagreement_is_queued() -> None:
    # The card exists for this set_code but at a different, non-Prismatic rarity
    # (the New artwork / Short Print class), not auto-corrected in v1.
    card = Card.objects.create(name="Fallen of Albaz")
    _printing(card, "CH01-EN001", "Ultra Rare")

    result = reconcile_products_to_printings([_listing("658227", "CH01-EN001", "Secret Rare")])

    assert result.queued_rarity_disagreement == 1
    assert UnmatchedProduct.objects.get().reason == UnmatchedReason.RARITY_DISAGREEMENT
    assert ExternalPriceId.objects.count() == 0


@pytest.mark.django_db
def test_multi_variant_group_is_queued_and_flags_the_generic_printing() -> None:
    card = Card.objects.create(name="Blue-Eyes White Dragon")
    printing = _printing(card, "LDK2-ENK01", "Common")
    products = [
        _listing("123525", "LDK2-ENK01", "Common", "Blue-Eyes White Dragon (Version 2)"),
        _listing("123620", "LDK2-ENK01", "Common", "Blue-Eyes White Dragon (Version 4)"),
        _listing("123621", "LDK2-ENK01", "Common", "Blue-Eyes White Dragon (Version 1)"),
    ]

    result = reconcile_products_to_printings(products)

    assert result.queued_multi_variant == 3
    assert UnmatchedProduct.objects.filter(reason=UnmatchedReason.MULTI_VARIANT).count() == 3
    assert ExternalPriceId.objects.count() == 0  # never auto-attached
    # The generic variant-NULL printing is flagged so the DS matcher later downgrades a
    # match on it to review rather than auto-materializing it.
    printing.refresh_from_db()
    assert printing.is_multi_variant is True
    assert result.multi_variant_flagged == 1


@pytest.mark.django_db
def test_multi_variant_prismatic_group_flags_the_provisional_printing() -> None:
    """A multi-variant group whose TCGCSV rarity is "Prismatic X" must flag the
    YGOPRODeck-seeded *provisional* "X" printing (resolved via the Prismatic strip), not
    look for a nonexistent "Prismatic X" row, otherwise a DS "X" row later matches that
    unflagged placeholder as EXACT."""
    card = Card.objects.create(name="Super Polymerization")
    provisional = _printing(card, "RA03-EN053", "Ultimate Rare")  # YGOPRODeck provisional
    products = [
        _listing("592540", "RA03-EN053", "Prismatic Ultimate Rare", "Super Polymerization (v1)"),
        _listing("592541", "RA03-EN053", "Prismatic Ultimate Rare", "Super Polymerization (v2)"),
    ]

    result = reconcile_products_to_printings(products)

    assert result.queued_multi_variant == 2
    provisional.refresh_from_db()
    assert provisional.is_multi_variant is True  # flagged via the Prismatic-strip resolution
    assert result.multi_variant_flagged == 1


@pytest.mark.django_db
def test_blank_external_id_is_skipped() -> None:
    card = Card.objects.create(name="Eternal Favorite")
    _printing(card, "MP25-EN172", "Ultra Rare")

    result = reconcile_products_to_printings([_listing("   ", "MP25-EN172", "Ultra Rare")])

    assert result.skipped_blank_external_id == 1
    assert result.exact_matched == 0
    assert ExternalPriceId.objects.count() == 0


@pytest.mark.django_db
def test_reconciliation_is_idempotent() -> None:
    card = Card.objects.create(name="Super Polymerization")
    _printing(card, "RA03-EN053", "Ultimate Rare")
    products = [_listing("592540", "RA03-EN053", "Prismatic Ultimate Rare")]

    reconcile_products_to_printings(products)
    second = reconcile_products_to_printings(products)

    # Second run: the printing now carries the canonical rarity, so the product
    # exact-matches it: no correction, no new external_id/alias, no duplicate.
    assert second.exact_matched == 1
    assert second.rarity_reconciled == 0
    assert second.external_ids_created == 0
    assert second.external_ids_existing == 1
    assert ExternalPriceId.objects.count() == 1
    assert PrintingAlias.objects.count() == 1


@pytest.mark.django_db
def test_existing_canonical_is_exact_matched_not_recreated() -> None:
    # Both provisional ("Ultimate Rare") and canonical ("Prismatic Ultimate Rare")
    # printings already exist. The Prismatic product exact-matches the canonical row
    # (pass 1), so the provisional is never touched and nothing collides.
    card = Card.objects.create(name="Super Polymerization")
    provisional = _printing(card, "RA03-EN053", "Ultimate Rare")
    canonical = _printing(card, "RA03-EN053", "Prismatic Ultimate Rare")

    result = reconcile_products_to_printings(
        [_listing("592540", "RA03-EN053", "Prismatic Ultimate Rare")]
    )

    assert result.exact_matched == 1
    assert result.rarity_reconciled == 0
    assert ExternalPriceId.objects.get().printing == canonical
    provisional.refresh_from_db()
    assert provisional.set_rarity == "Ultimate Rare"  # untouched, not deleted
    assert CardPrinting.objects.count() == 2


@pytest.mark.django_db
def test_prismatic_does_not_reclaim_an_exactly_matched_printing() -> None:
    # TCGCSV lists both "Ultimate Rare" (exact) and "Prismatic Ultimate Rare" (fallback)
    # for one set_code. The exact match claims the printing in pass 1, so the Prismatic
    # product is queued as a disagreement rather than corrupting the claimed printing.
    card = Card.objects.create(name="Super Polymerization")
    printing = _printing(card, "RA03-EN053", "Ultimate Rare")

    result = reconcile_products_to_printings(
        [
            _listing("111", "RA03-EN053", "Ultimate Rare"),  # exact
            _listing("592540", "RA03-EN053", "Prismatic Ultimate Rare"),  # fallback → claimed base
        ]
    )

    assert result.exact_matched == 1
    assert result.rarity_reconciled == 0
    assert result.queued_rarity_disagreement == 1
    printing.refresh_from_db()
    assert printing.set_rarity == "Ultimate Rare"  # not corrupted to Prismatic
    assert ExternalPriceId.objects.get().external_id == "111"
    assert UnmatchedProduct.objects.get().external_id == "592540"


@pytest.mark.django_db
def test_external_id_conflict_is_queued_not_silently_accepted() -> None:
    """If a productId already maps to a *different* printing (provider-side drift, a
    manual edit, or a prior bad run), reconciliation must not report it matched while
    leaving the stale mapping: it queues a conflict for human repair."""
    card = Card.objects.create(name="Eternal Favorite")
    other = _printing(card, "PHNI-EN038", "Secret Rare")  # the printing the id wrongly points at
    _printing(card, "MP25-EN172", "Ultra Rare")  # what 651572 actually resolves to
    ExternalPriceId.objects.create(provider=Provider.TCGCSV, external_id="651572", printing=other)

    result = reconcile_products_to_printings([_listing("651572", "MP25-EN172", "Ultra Rare")])

    assert result.exact_matched == 0
    assert result.queued_external_id_conflict == 1
    assert UnmatchedProduct.objects.get().reason == UnmatchedReason.EXTERNAL_ID_CONFLICT
    assert ExternalPriceId.objects.get(external_id="651572").printing == other  # left for a human


@pytest.mark.django_db
def test_external_id_conflict_on_prismatic_path_leaves_printing_uncorrected() -> None:
    """A Prismatic fallback whose id already maps elsewhere is queued before any
    mutation: the provisional rarity is left uncorrected and no alias is written."""
    card = Card.objects.create(name="Super Polymerization")
    base = _printing(card, "RA03-EN053", "Ultimate Rare")
    other = _printing(card, "PHNI-EN038", "Secret Rare")
    ExternalPriceId.objects.create(provider=Provider.TCGCSV, external_id="592540", printing=other)

    result = reconcile_products_to_printings(
        [_listing("592540", "RA03-EN053", "Prismatic Ultimate Rare")]
    )

    assert result.rarity_reconciled == 0
    assert result.queued_external_id_conflict == 1
    base.refresh_from_db()
    assert base.set_rarity == "Ultimate Rare"  # not corrected on conflict
    assert PrintingAlias.objects.count() == 0  # no alias written on conflict
    assert ExternalPriceId.objects.get(external_id="592540").printing == other
