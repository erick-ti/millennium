from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from apps.pricing.providers.base import (
    JsonFetcher,
    PriceData,
    PricingProvider,
    ProductListing,
    fetch_json,
)

logger = structlog.get_logger(__name__)

# TCGCSV mirrors TCGplayer's catalog as flat JSON. YuGiOh is categoryId 2 — a
# stable id, so the /categories endpoint is not fetched. Per group we read the
# product catalog (printing identity: set_code + rarity) and the price rows
# separately; they are joined downstream by productId.
_BASE_URL = "https://tcgcsv.com/tcgplayer"
_YUGIOH_CATEGORY_ID = 2

# "Normal" is TCGCSV's subtype for sealed products (booster boxes, decks), which
# also lack an extendedData "Number"; neither is a single card, so both forms are
# dropped (recon Q7/Q8). The single-card editions are the other subtypes (1st
# Edition / Unlimited / Limited), mapped to `Edition` when snapshots are written.
_SEALED_SUBTYPE = "Normal"

# extendedData entry names carrying the single-card printing identity.
_NUMBER_FIELD = "Number"  # the set_code, e.g. "RA03-EN053"
_RARITY_FIELD = "Rarity"  # TCGCSV-canonical rarity, e.g. "Prismatic Ultimate Rare"

# Absolute bootstrap floors for the FIRST run only (no history yet), the
# YgoprodeckProvider pattern: the live YuGiOh catalog has ~650 groups and tens of
# thousands of single-card products/price rows and only grows, so a value well under
# that never false-rejects but catches a grossly truncated fetch (a cut connection
# yields a handful of rows). Once a prior successful sync exists, the orchestration
# injects the precise compare-to-previous floor (last_good * (1 - tolerance)) via
# `min_products` / `min_price_rows`, superseding these (DECISIONS 2026-05-24 slice 3).
_MIN_EXPECTED_GROUPS = 100
_MIN_EXPECTED_PRODUCTS = 1000


class TcgcsvProvider(PricingProvider):
    """Single-card prices + product catalog from TCGCSV's TCGplayer mirror.

    Implements ``fetch_prices`` (the ``PricingProvider`` contract) and adds
    ``fetch_products`` — the catalog half the matching slice needs to link a
    productId to a ``CardPrinting`` before any price can attach (see
    ``PricingProvider`` for why that method is concrete, not on the ABC). Both
    read the same group list (fetched once per instance); sealed products and
    "Normal" price rows are dropped as non-single-card. Fail-closed on an
    unsuccessful envelope or a grossly truncated catalog, like ``YgoprodeckProvider``.
    """

    def __init__(
        self,
        fetch: JsonFetcher = fetch_json,
        *,
        min_groups: int | None = None,
        min_products: int | None = None,
        min_price_rows: int | None = None,
    ) -> None:
        self._fetch = fetch
        # None → the absolute bootstrap floor (first run, no history). The
        # orchestration passes last_good * (1 - tolerance) once history exists.
        self._min_groups = _MIN_EXPECTED_GROUPS if min_groups is None else min_groups
        self._min_products = _MIN_EXPECTED_PRODUCTS if min_products is None else min_products
        self._min_price_rows = _MIN_EXPECTED_PRODUCTS if min_price_rows is None else min_price_rows
        # Cached so fetch_products + fetch_prices on one instance fetch the group
        # list once, not twice.
        self._group_cache: list[dict[str, Any]] | None = None

    # -- public API ----------------------------------------------------------

    def fetch_products(self) -> list[ProductListing]:
        """Yield every single-card product TCGCSV lists, across all groups."""
        products: list[ProductListing] = []
        skipped = 0
        for group_id, set_name in self._iter_group_targets():
            payload = self._fetch(self._products_url(group_id))
            for raw in _require_results(payload, "products"):
                listing = _normalize_product(raw, set_name)
                if listing is None:
                    skipped += 1
                else:
                    products.append(listing)
        if len(products) < self._min_products:
            raise ValueError(
                f"TCGCSV returned {len(products)} single-card products, below the "
                f"sanity floor of {self._min_products} — refusing a likely-truncated "
                f"catalog (the live catalog has tens of thousands)."
            )
        if skipped:
            # Sealed products dominate this count and are expected; surfaced (not
            # silent) so a sudden shift is visible in the run and daily logs.
            logger.info("tcgcsv_sync.skipped_non_single_products", count=skipped)
        return products

    def fetch_prices(self) -> list[PriceData]:
        """Yield every non-sealed price row TCGCSV reports, across all groups.

        Drops only ``"Normal"`` (sealed) rows — it does NOT by itself guarantee
        each row is a single card. That gate is downstream: the ingestion slice
        writes a snapshot only when a row's ``productId`` resolves to a
        ``CardPrinting`` via ``external_price_ids``, which only matched single-card
        products obtain (sealed products have no ``extendedData.Number`` to match
        on). So ingestion must **join on productId first, then map ``subtype_name``
        to an ``Edition``**: an unrecognized/edge subtype (e.g. the sealed-deck
        ``"Limited"`` TCGCSV puts on Tokens, which have no YGOPRODeck card) is then
        skipped at the join rather than forced through edition mapping or aborting
        the run. Pre-filtering here against the product catalog would only
        duplicate a weaker form of that join.
        """
        prices: list[PriceData] = []
        skipped = 0
        for group_id, _set_name in self._iter_group_targets():
            payload = self._fetch(self._prices_url(group_id))
            for raw in _require_results(payload, "prices"):
                price = _normalize_price(raw)
                if price is None:
                    skipped += 1
                else:
                    prices.append(price)
        if len(prices) < self._min_price_rows:
            raise ValueError(
                f"TCGCSV returned {len(prices)} single-card price rows, below the "
                f"sanity floor of {self._min_price_rows} — refusing a likely-"
                f"truncated catalog."
            )
        if skipped:
            logger.info("tcgcsv_sync.skipped_sealed_price_rows", count=skipped)
        return prices

    # -- internals -----------------------------------------------------------

    def _iter_group_targets(self) -> Iterator[tuple[int, str]]:
        """Yield (groupId, set_name) for each valid group, fetching groups once."""
        for group in self._groups():
            group_id = group.get("groupId")
            if group_id is None:
                # A group with no id can't be addressed for products/prices; skip
                # defensively (gross loss is caught by the product/price floors).
                continue
            yield int(group_id), str(group.get("name", "")).strip()

    def _groups(self) -> list[dict[str, Any]]:
        cache = self._group_cache
        if cache is None:
            payload = self._fetch(self._groups_url())
            cache = _require_results(payload, "groups")
            if len(cache) < self._min_groups:
                raise ValueError(
                    f"TCGCSV returned {len(cache)} groups, below the sanity floor "
                    f"of {self._min_groups} — refusing a likely-truncated group list."
                )
            self._group_cache = cache
        return cache

    def _groups_url(self) -> str:
        return f"{_BASE_URL}/{_YUGIOH_CATEGORY_ID}/groups"

    def _products_url(self, group_id: int) -> str:
        return f"{_BASE_URL}/{_YUGIOH_CATEGORY_ID}/{group_id}/products"

    def _prices_url(self, group_id: int) -> str:
        return f"{_BASE_URL}/{_YUGIOH_CATEGORY_ID}/{group_id}/prices"


def _require_results(payload: Any, label: str) -> list[Any]:
    """Validate a TCGCSV envelope and return its ``results`` list.

    Every TCGCSV response is ``{success, results, errors, ...}``. Fail closed
    when ``success`` is not ``True`` or ``results`` is not a list, so an upstream
    error body (a 200 carrying ``success: false``) can't masquerade as empty data
    — the YgoprodeckProvider posture. An empty-but-valid per-group ``results`` is
    allowed (some groups have no single-card products); gross truncation is caught
    by the aggregate floors in the callers, not here.
    """
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError(
            f"TCGCSV {label} response was not successful — refusing to treat an "
            f"upstream failure as empty data."
        )
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError(
            f"TCGCSV {label} response has no 'results' list — refusing to treat an "
            f"upstream failure as empty data."
        )
    return results


def _normalize_product(raw: dict[str, Any], set_name: str) -> ProductListing | None:
    """Build a ``ProductListing`` from a TCGCSV product, or ``None`` to skip it.

    Skips anything that isn't a usable single card: sealed products (no
    ``extendedData.Number``), and the rare malformed single card missing a
    rarity / id / name. ``set_code`` / ``set_rarity`` are trimmed only (they are
    natural-key text the matching slice canonicalizes — DECISIONS 2026-05-21),
    while ``name`` keeps its variant parenthetical verbatim for the matcher.
    """
    extended = _extended_data(raw)
    set_code = extended.get(_NUMBER_FIELD, "").strip()
    if not set_code:
        return None  # sealed product (booster box / deck), not a single card
    set_rarity = extended.get(_RARITY_FIELD, "").strip()
    product_id = raw.get("productId")
    name = str(raw.get("name", "")).strip()
    if not set_rarity or product_id is None or not name:
        return None  # a single-card product missing identity fields; can't match
    return ProductListing(
        external_id=str(product_id),
        set_code=set_code,
        set_rarity=set_rarity,
        name=name,
        set_name=set_name,
    )


def _normalize_price(raw: dict[str, Any]) -> PriceData | None:
    """Build a ``PriceData`` from a TCGCSV price row, or ``None`` to skip it.

    Drops "Normal" rows (sealed products). The five price points are nullable —
    a provider may omit any — and are converted via ``str`` so the stored Decimal
    is exact rather than carrying binary-float noise.
    """
    subtype = raw.get("subTypeName")
    if subtype == _SEALED_SUBTYPE:
        return None
    product_id = raw.get("productId")
    if product_id is None:
        return None
    return PriceData(
        external_id=str(product_id),
        subtype_name=str(subtype) if subtype is not None else None,
        low_price=_to_decimal(raw.get("lowPrice")),
        mid_price=_to_decimal(raw.get("midPrice")),
        high_price=_to_decimal(raw.get("highPrice")),
        market_price=_to_decimal(raw.get("marketPrice")),
        direct_low_price=_to_decimal(raw.get("directLowPrice")),
    )


def _extended_data(raw: dict[str, Any]) -> dict[str, str]:
    """Flatten TCGCSV's ``extendedData`` list of ``{name, value}`` to a name→value map."""
    result: dict[str, str] = {}
    for entry in raw.get("extendedData") or []:
        if isinstance(entry, dict):
            name = entry.get("name")
            value = entry.get("value")
            if isinstance(name, str) and value is not None:
                result[name] = str(value)
    return result


def _to_decimal(value: Any) -> Decimal | None:
    # TCGCSV prices are JSON numbers in USD (2 dp). Convert via str so we get the
    # exact decimal — Decimal(0.67) would carry binary-float noise. A model-level
    # CHECK on PriceSnapshot (a 2c obligation) is the all-source backstop; this
    # boundary guard is defense-in-depth: drop values that can't be a real price.
    if value is None:
        return None  # the provider didn't report this point
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        logger.warning("tcgcsv_sync.unparseable_price", value=repr(value))
        return None
    # NaN/Infinity (Python's json parser accepts both literals) or a negative are
    # structurally impossible for a price — treat as "not reported" rather than
    # poison stored prices/aggregations. Per-field skip, not raise, so one corrupt
    # value can't abort a tens-of-thousands-row daily sync (the ygoprodeck
    # garbage-rarity posture). is_finite() is checked first so the `< 0` comparison
    # never runs on a NaN, which would itself raise.
    if not result.is_finite() or result < 0:
        logger.warning("tcgcsv_sync.invalid_price", value=str(result))
        return None
    return result
