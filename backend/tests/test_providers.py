from __future__ import annotations

from typing import Any

import pytest

from apps.pricing.providers.base import MetadataProvider, PricingProvider
from apps.pricing.providers.ygoprodeck import YgoprodeckProvider


def _provider(payload: dict[str, Any], *, min_cards: int = 1) -> YgoprodeckProvider:
    """A YgoprodeckProvider whose network fetch is replaced by a fixture payload.

    min_cards defaults to 1 so small fixtures don't trip the cardinality floor;
    the floor itself is exercised by test_truncated_dump_below_floor_fails_closed.
    """
    return YgoprodeckProvider(fetch=lambda _url: payload, min_cards=min_cards)


def test_normalizes_card_and_printings() -> None:
    provider = _provider(
        {
            "data": [
                {
                    "id": 89631139,
                    "name": "Blue-Eyes White Dragon",
                    "card_sets": [
                        {
                            "set_name": "Legend of Blue Eyes White Dragon",
                            "set_code": "LOB-001",
                            "set_rarity": "Ultra Rare",
                        },
                        {
                            "set_name": "Starter Deck: Kaiba",
                            "set_code": "SDK-001",
                            "set_rarity": "Ultra Rare",
                        },
                    ],
                }
            ]
        }
    )

    cards = list(provider.fetch_card_metadata())

    assert len(cards) == 1
    card = cards[0]
    assert card.passcode == 89631139
    assert card.name == "Blue-Eyes White Dragon"
    assert {(p.set_code, p.set_rarity) for p in card.printings} == {
        ("LOB-001", "Ultra Rare"),
        ("SDK-001", "Ultra Rare"),
    }
    assert all(p.variant_label is None for p in card.printings)


def test_skips_cards_missing_id_or_name() -> None:
    provider = _provider(
        {
            "data": [
                {"name": "No Passcode"},
                {"id": 12345},
                {"id": 46986414, "name": "Dark Magician"},
            ]
        }
    )

    cards = list(provider.fetch_card_metadata())

    assert [c.passcode for c in cards] == [46986414]


def test_trims_whitespace_and_skips_blank_set_code() -> None:
    provider = _provider(
        {
            "data": [
                {
                    "id": 1,
                    "name": "  Spacey Card  ",
                    "card_sets": [
                        {"set_name": "  A Set  ", "set_code": " AB-001 ", "set_rarity": " Common "},
                        {"set_name": "Bad", "set_code": "   ", "set_rarity": "Common"},
                        {"set_name": "Also Bad", "set_code": "CD-002", "set_rarity": ""},
                    ],
                }
            ]
        }
    )

    (card,) = provider.fetch_card_metadata()

    assert card.name == "Spacey Card"
    assert len(card.printings) == 1
    printing = card.printings[0]
    assert (printing.set_code, printing.set_rarity, printing.set_name) == (
        "AB-001",
        "Common",
        "A Set",
    )


def test_dedupes_duplicate_set_code_and_rarity() -> None:
    """Alt-arts repeat (set_code, set_rarity) with no label; keep the first."""
    provider = _provider(
        {
            "data": [
                {
                    "id": 89631139,
                    "name": "Blue-Eyes White Dragon",
                    "card_sets": [
                        {
                            "set_name": "Legendary Decks II",
                            "set_code": "LDK2-ENK01",
                            "set_rarity": "Common",
                        },
                        {
                            "set_name": "Legendary Decks II",
                            "set_code": "LDK2-ENK01",
                            "set_rarity": "Common",
                        },
                    ],
                }
            ]
        }
    )

    (card,) = provider.fetch_card_metadata()

    assert len(card.printings) == 1


def test_card_without_sets_yields_no_printings() -> None:
    provider = _provider({"data": [{"id": 1, "name": "Unreleased Card"}]})

    (card,) = provider.fetch_card_metadata()

    assert card.printings == ()


def test_decodes_html_entities_in_names() -> None:
    """YGOPRODeck serves HTML-encoded names; the adapter decodes them so stored
    display text isn't polluted with entities (normalized_name correctness for
    the same case is covered by test_cards.test_save_derives_normalized_name)."""
    provider = _provider(
        {
            "data": [
                {
                    "id": 64163367,
                    "name": "The Fallen &amp; The Virtuous",
                    "card_sets": [
                        {
                            "set_name": "Duelist Nexus &amp; Friends",
                            "set_code": "DUNE-EN001",
                            "set_rarity": "Secret Rare",
                        }
                    ],
                }
            ]
        }
    )

    (card,) = provider.fetch_card_metadata()

    assert card.name == "The Fallen & The Virtuous"
    assert card.printings[0].set_name == "Duelist Nexus & Friends"


def test_skips_numeric_garbage_rarity() -> None:
    """YGOPRODeck reports rarity "2" for L5DD-ENC09 (a known data bug); the
    adapter must drop it rather than seed it into a natural key — TCGCSV is
    canonical for rarity and reconciles it later. Valid printings survive."""
    provider = _provider(
        {
            "data": [
                {
                    "id": 14558127,
                    "name": "Ash Blossom & Joyous Spring",
                    "card_sets": [
                        {
                            "set_name": "Maximum Gold",
                            "set_code": "MGED-EN007",
                            "set_rarity": "Premium Gold Rare",
                        },
                        {
                            "set_name": "Legendary Duelists: Duels From the Deep",
                            "set_code": "L5DD-ENC09",
                            "set_rarity": "2",
                        },
                    ],
                }
            ]
        }
    )

    (card,) = provider.fetch_card_metadata()

    assert [(p.set_code, p.set_rarity) for p in card.printings] == [
        ("MGED-EN007", "Premium Gold Rare")
    ]


def test_missing_data_key_fails_closed() -> None:
    """A response with no 'data' list is an upstream failure, not empty success."""
    with pytest.raises(ValueError, match="data"):
        _provider({}).fetch_card_metadata()


def test_non_list_data_fails_closed() -> None:
    with pytest.raises(ValueError, match="data"):
        _provider({"data": {"error": "rate limited"}}).fetch_card_metadata()


def test_zero_usable_cards_fails_closed() -> None:
    with pytest.raises(ValueError, match="zero usable"):
        _provider({"data": [{"name": "no passcode"}]}).fetch_card_metadata()


def test_truncated_dump_below_floor_fails_closed() -> None:
    """A non-empty but implausibly small dump (vs the ~14k full dump) is treated
    as a likely-truncated response, not a valid partial catalog."""
    payload = {"data": [{"id": 1, "name": "Card One"}, {"id": 2, "name": "Card Two"}]}
    with pytest.raises(ValueError, match="floor"):
        _provider(payload, min_cards=5).fetch_card_metadata()


def test_provider_roles_are_abstract() -> None:
    """Both roles are abstract markers — neither can be instantiated directly."""
    with pytest.raises(TypeError):
        MetadataProvider()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        PricingProvider()  # type: ignore[abstract]
