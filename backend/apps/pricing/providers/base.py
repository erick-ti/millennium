from __future__ import annotations

import abc
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

# Shared HTTP transport ------------------------------------------------------

_USER_AGENT = "millennium/0.1 (personal Yu-Gi-Oh collection tracker)"
_DEFAULT_TIMEOUT = httpx.Timeout(30.0)

#: A callable that GETs a URL and returns decoded JSON. Providers accept one of
#: these (defaulting to ``fetch_json``) so tests can inject fixture payloads
#: instead of hitting the network.
JsonFetcher = Callable[[str], Any]


def fetch_json(url: str) -> Any:
    """GET ``url`` and return its decoded JSON body, raising on HTTP errors."""
    response = httpx.get(
        url,
        timeout=_DEFAULT_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.json()


# Normalized records ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PrintingMetadata:
    """One catalog printing of a card, provider-neutral.

    ``variant_label`` is ``None`` for bulk metadata feeds: YGOPRODeck doesn't
    label alt-arts, so the rare "same (set_code, set_rarity), different artwork"
    case is left for the Phase 3 import-matching engine to disambiguate.

    ``set_rarity`` from a metadata provider is *provisional*, not canonical.
    YGOPRODeck rarity both carries sporadic bugs (L5DD-ENC09 reports ``"2"``) and
    *systematically* disagrees with TCGCSV on whole classes (RA03 ``"Ultimate
    Rare"`` vs TCGCSV ``"Prismatic Ultimate Rare"``; CH01 ``"New artwork"`` vs
    ``"Ultra Rare"``; LOB ``"Short Print"`` vs ``"Common"``). TCGCSV is the source
    of truth for rarity (recon PHASE_1A5_FINDINGS; DECISIONS 2026-05-23). A
    metadata sync seeds best-effort rarities and drops only *blatant* garbage
    (blank/numeric) at its boundary — it does NOT reconcile the disagreement
    class. The TCGCSV-ingestion slice reconciles by updating the seeded printing
    in place when it's the same real printing (FK refs are by ``id``, so this is
    a column update, not a key migration) and splitting into separate
    ``variant_label`` rows when TCGCSV proves distinct sellable variants — never
    duplicating the same canonical key — and review-queues the unresolved.
    """

    set_code: str
    set_rarity: str
    set_name: str
    variant_label: str | None = None


@dataclass(frozen=True, slots=True)
class CardMetadata:
    """A card's identity plus its catalog printings, provider-neutral.

    ``passcode`` is required because the only metadata provider today
    (YGOPRODeck) gives every card one. TCGCSV-only entities such as tokens have
    no passcode (DECISIONS 2026-05-18); representing them means widening this to
    ``passcode: int | None`` with a fallback identity key — deferred to the slice
    that adds TCGCSV catalog ingestion (see ``PricingProvider``).

    ``archetype`` is the card's Yu-Gi-Oh archetype (e.g. "Blue-Eyes"), supplied by
    the provider; ``None`` when the card has none (~40% don't) — never coerce a
    missing archetype to ``""`` (NULL is the canonical "no archetype").
    """

    passcode: int
    name: str
    archetype: str | None = None
    printings: tuple[PrintingMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class PriceData:
    """A single provider price row for a printing+subtype on the fetch day.

    Produced by ``PricingProvider.fetch_prices`` and consumed when writing
    ``PriceSnapshot``: ``external_id`` matches back to a printing (via
    ``external_price_ids``); ``subtype_name`` is the raw provider edition (e.g.
    TCGCSV ``"1st Edition"``, normalized to an ``Edition`` downstream and kept
    verbatim as the snapshot's ``source_subtype_name``); the five price points
    map 1:1 to the snapshot's columns (a provider may report any subset — absent
    ones are ``None``). ``TcgcsvProvider`` is the first implementation.
    """

    external_id: str
    subtype_name: str | None
    low_price: Decimal | None = None
    mid_price: Decimal | None = None
    high_price: Decimal | None = None
    market_price: Decimal | None = None
    direct_low_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ProductListing:
    """A pricing provider's sellable single-card product, matched to a
    ``CardPrinting`` by ``(set_code, set_rarity)`` before any price attaches.

    The pricing counterpart to ``PrintingMetadata``: a metadata provider supplies
    the catalog of printings that *exist*; a pricing provider supplies the
    products it *sells*, which the matching slice links to those printings (then
    prices via ``PriceData``, joined by ``external_id``). ``set_rarity`` here is
    the provider's *canonical* rarity — TCGCSV is the source of truth (DECISIONS
    2026-05-23), so it is what a provisional metadata rarity gets reconciled
    against. ``name`` is the raw product name (e.g. ``"Blue-Eyes White Dragon
    (Version 1)"``); its parenthetical is the only signal distinguishing
    same-``(set_code, set_rarity)`` variant artworks (recon Q5), so it is kept
    verbatim for the matcher rather than parsed here.
    """

    external_id: str
    set_code: str
    set_rarity: str
    name: str
    set_name: str = ""


# Provider roles -------------------------------------------------------------


class MetadataProvider(abc.ABC):
    """A source of card *metadata* — identity and catalog printings.

    Feeds ``cards`` / ``card_printings``; YGOPRODeck is the only one today.
    Deliberately split from ``PricingProvider``: metadata sources carry no
    prices, pricing sources carry no card identity, and both deliver data in
    bulk rather than per-id, so one combined interface fits neither cleanly.
    """

    @abc.abstractmethod
    def fetch_card_metadata(self) -> Iterable[CardMetadata]:
        """Yield every card the provider knows, each with its printings."""


class PricingProvider(abc.ABC):
    """A source of *prices* (and the sellable-product catalog they attach to) for
    printings. The pricing counterpart to ``MetadataProvider`` (see that
    docstring for why the roles are separate). ``TcgcsvProvider`` is the first
    implementation (Phase 2 price ingestion).

    ``fetch_prices`` yields ``PriceData`` — a provider id plus price points. On
    its own that can't be matched to a printing on the first run (``external_price_ids``
    is empty until something populates it), so a concrete pricing provider also
    surfaces its product catalog as ``ProductListing`` (``external_id`` +
    ``(set_code, set_rarity)`` + raw name) for the matching slice to link to a
    ``CardPrinting`` before pricing. That catalog method is deliberately left off
    this ABC while there is one provider (n=1): ``ProductListing`` is the
    provider-neutral contract the matcher consumes; *how* a provider produces it
    stays concrete until a second provider shows what generalizes.

    Still deferred (token slice): TCGCSV is the *only* catalog source for entities
    absent from YGOPRODeck (tokens — DECISIONS 2026-05-18). Creating passcode-null
    ``Card`` / ``CardPrinting`` rows for them needs ``CardMetadata`` to gain a
    passcode-optional form (see its docstring); until then a pricing provider
    matches its products to *existing* YGOPRODeck-seeded printings only, and the
    unmatched (tokens included) go to the reconciliation slice's review queue
    rather than being created.
    """

    @abc.abstractmethod
    def fetch_prices(self) -> Iterable[PriceData]:
        """Yield every price row the provider currently reports."""
