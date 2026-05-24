from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from apps.pricing.providers.base import MetadataProvider, PricingProvider, ProductListing
from apps.pricing.providers.tcgcsv import TcgcsvProvider
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


# -- TcgcsvProvider ----------------------------------------------------------


def _envelope(results: list[Any], *, success: bool = True) -> dict[str, Any]:
    """The TCGCSV response envelope shared by groups / products / prices."""
    return {"success": success, "results": results, "errors": [], "totalItems": len(results)}


def _tcgcsv(
    *,
    groups: list[dict[str, Any]],
    products: dict[int, list[dict[str, Any]]] | None = None,
    prices: dict[int, list[dict[str, Any]]] | None = None,
    min_groups: int = 1,
    min_products: int = 1,
    min_price_rows: int = 1,
) -> TcgcsvProvider:
    """A TcgcsvProvider whose network fetch is replaced by a URL-routing fixture.

    Floors default to 1 so small fixtures don't trip the truncation guards; the
    guards themselves are exercised by the *_below_floor_* tests.
    """
    products = products or {}
    prices = prices or {}

    def fetch(url: str) -> Any:
        segments = url.split("/")
        kind = segments[-1]
        if kind == "groups":
            return _envelope(groups)
        group_id = int(segments[-2])
        table = products if kind == "products" else prices
        return _envelope(table.get(group_id, []))

    return TcgcsvProvider(
        fetch=fetch, min_groups=min_groups, min_products=min_products, min_price_rows=min_price_rows
    )


def _product(
    product_id: int, number: str, rarity: str, name: str = "Some Card"
) -> dict[str, Any]:
    return {
        "productId": product_id,
        "name": name,
        "extendedData": [
            {"name": "Number", "displayName": "Number", "value": number},
            {"name": "Rarity", "displayName": "Rarity", "value": rarity},
            {"name": "Attribute", "displayName": "Attribute", "value": "DARK"},
        ],
    }


def _sealed_product(product_id: int, name: str = "Booster Box") -> dict[str, Any]:
    """A sealed product: no extendedData 'Number', so it's not a single card."""
    return {
        "productId": product_id,
        "name": name,
        "extendedData": [{"name": "Description", "displayName": "Description", "value": "Sealed"}],
    }


def _price(
    product_id: int,
    subtype: str,
    *,
    low: float | None = None,
    mid: float | None = None,
    high: float | None = None,
    market: float | None = None,
    direct: float | None = None,
) -> dict[str, Any]:
    return {
        "productId": product_id,
        "lowPrice": low,
        "midPrice": mid,
        "highPrice": high,
        "marketPrice": market,
        "directLowPrice": direct,
        "subTypeName": subtype,
    }


def test_fetch_products_normalizes_and_drops_sealed() -> None:
    provider = _tcgcsv(
        groups=[{"groupId": 23656, "name": "Quarter Century Stampede"}],
        products={
            23656: [
                _product(
                    592540, "RA03-EN053", "Prismatic Ultimate Rare", "Super Polymerization (PUR)"
                ),
                _sealed_product(653375, "THE CHRONICLES DECK"),
            ]
        },
    )

    listings = provider.fetch_products()

    assert listings == [
        ProductListing(
            external_id="592540",
            set_code="RA03-EN053",
            set_rarity="Prismatic Ultimate Rare",
            name="Super Polymerization (PUR)",
            set_name="Quarter Century Stampede",
        )
    ]


def test_fetch_products_keeps_variant_parenthetical() -> None:
    """One YGOPRODeck (set_code, rarity) → many TCGCSV products; the variant lives
    only in the product name parenthetical, kept verbatim for the matching slice."""
    provider = _tcgcsv(
        groups=[{"groupId": 1841, "name": "Legendary Decks II"}],
        products={
            1841: [
                _product(123525, "LDK2-ENK01", "Common", "Blue-Eyes White Dragon (Version 2)"),
                _product(123620, "LDK2-ENK01", "Common", "Blue-Eyes White Dragon (Version 4)"),
                _product(123621, "LDK2-ENK01", "Common", "Blue-Eyes White Dragon (Version 1)"),
            ]
        },
    )

    listings = provider.fetch_products()

    assert {pl.external_id for pl in listings} == {"123525", "123620", "123621"}
    assert {(pl.set_code, pl.set_rarity) for pl in listings} == {("LDK2-ENK01", "Common")}
    assert {pl.name for pl in listings} == {
        "Blue-Eyes White Dragon (Version 2)",
        "Blue-Eyes White Dragon (Version 4)",
        "Blue-Eyes White Dragon (Version 1)",
    }


def test_fetch_products_skips_single_card_missing_rarity() -> None:
    provider = _tcgcsv(
        groups=[{"groupId": 1, "name": "A Set"}],
        products={
            1: [
                _product(10, "AB-001", "Common"),
                {
                    "productId": 11,
                    "name": "No Rarity",
                    "extendedData": [{"name": "Number", "value": "AB-002"}],
                },
            ]
        },
    )

    listings = provider.fetch_products()

    assert [pl.external_id for pl in listings] == ["10"]


def test_fetch_prices_drops_sealed_and_keeps_exact_decimals() -> None:
    provider = _tcgcsv(
        groups=[{"groupId": 330, "name": "Legend of Blue Eyes White Dragon"}],
        prices={
            330: [
                _price(21747, "1st Edition", low=0.13, mid=0.47, high=19.99, market=0.44),
                _price(21747, "Unlimited", low=0.10, mid=0.30, high=5.0, market=0.25),
                _price(653375, "Normal", low=20.79, mid=23.45, high=50.0, market=22.41),
            ]
        },
    )

    rows = provider.fetch_prices()

    assert {r.subtype_name for r in rows} == {"1st Edition", "Unlimited"}  # "Normal" dropped
    first = next(r for r in rows if r.subtype_name == "1st Edition")
    assert first.external_id == "21747"
    assert first.market_price == Decimal("0.44")
    assert first.high_price == Decimal("19.99")
    assert first.direct_low_price is None


def test_fetch_prices_drops_non_finite_negative_and_unparseable() -> None:
    """NaN/Infinity (which Python's JSON parser accepts), negatives, and
    unparseable values are dropped to None at the boundary rather than poisoning
    stored prices; valid points on the same row survive."""
    bad_row = {
        "productId": 10,
        "lowPrice": "oops",  # unparseable
        "midPrice": -1.0,  # negative
        "highPrice": float("nan"),  # non-finite
        "marketPrice": 0.44,  # valid
        "directLowPrice": float("inf"),  # non-finite
        "subTypeName": "1st Edition",
    }
    provider = _tcgcsv(groups=[{"groupId": 1, "name": "A"}], prices={1: [bad_row]})

    (row,) = provider.fetch_prices()

    assert row.market_price == Decimal("0.44")
    assert row.low_price is None
    assert row.mid_price is None
    assert row.high_price is None
    assert row.direct_low_price is None


def test_groups_fetched_once_across_methods() -> None:
    """fetch_products + fetch_prices on one instance fetch the group list once."""
    calls: list[str] = []

    def fetch(url: str) -> Any:
        calls.append(url)
        if url.endswith("/groups"):
            return _envelope([{"groupId": 1, "name": "A"}])
        if url.endswith("/products"):
            return _envelope([_product(10, "AB-001", "Common")])
        return _envelope([_price(10, "1st Edition", market=1.0)])

    provider = TcgcsvProvider(fetch=fetch, min_groups=1, min_products=1, min_price_rows=1)
    provider.fetch_products()
    provider.fetch_prices()

    assert sum(1 for url in calls if url.endswith("/groups")) == 1


def test_unsuccessful_envelope_fails_closed() -> None:
    provider = TcgcsvProvider(
        fetch=lambda _url: {"success": False, "results": [], "errors": ["boom"]}, min_groups=1
    )
    with pytest.raises(ValueError, match="not successful"):
        provider.fetch_products()


def test_non_list_results_fails_closed() -> None:
    provider = TcgcsvProvider(
        fetch=lambda _url: {"success": True, "results": {"oops": 1}}, min_groups=1
    )
    with pytest.raises(ValueError, match="results"):
        provider.fetch_products()


def test_truncated_group_list_below_floor_fails_closed() -> None:
    with pytest.raises(ValueError, match="floor"):
        _tcgcsv(groups=[{"groupId": 1, "name": "A"}], min_groups=5).fetch_products()


def test_truncated_products_below_floor_fails_closed() -> None:
    provider = _tcgcsv(
        groups=[{"groupId": 1, "name": "A"}],
        products={1: [_product(10, "AB-001", "Common")]},
        min_products=5,
    )
    with pytest.raises(ValueError, match="floor"):
        provider.fetch_products()


def test_truncated_prices_below_floor_fails_closed() -> None:
    provider = _tcgcsv(
        groups=[{"groupId": 1, "name": "A"}],
        prices={1: [_price(10, "1st Edition", market=1.0)]},
        min_price_rows=5,
    )
    with pytest.raises(ValueError, match="floor"):
        provider.fetch_prices()


def test_tcgcsv_implements_pricing_provider() -> None:
    assert isinstance(TcgcsvProvider(fetch=lambda _url: _envelope([])), PricingProvider)
