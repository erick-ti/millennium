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
    """

    passcode: int
    name: str
    printings: tuple[PrintingMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class PriceData:
    """A single provider price row for a printing+subtype on the fetch day.

    Provisional: the concrete shape is finalized when the first
    ``PricingProvider`` (TCGCSV) lands in Phase 2's price-ingestion slice. Fields
    mirror what ``PriceSnapshot`` consumes — an ``external_id`` to match back to
    a printing, the raw provider ``subtype_name`` (normalized to an edition
    downstream), and the price points (a provider may report any subset).
    """

    external_id: str
    subtype_name: str | None
    low_price: Decimal | None = None
    mid_price: Decimal | None = None
    high_price: Decimal | None = None
    market_price: Decimal | None = None
    direct_low_price: Decimal | None = None


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
    """A source of *prices* for already-known printings.

    The pricing counterpart to ``MetadataProvider`` (see that docstring for why
    the roles are separate). The concrete contract — and the final shape of
    ``PriceData`` — is settled when the first implementation (TCGCSV) lands in
    Phase 2's price-ingestion slice; declared now so the role split is explicit
    from the start of the provider layer.

    Open obligation for that slice: TCGCSV is also the *only* catalog source for
    entities absent from YGOPRODeck (tokens — DECISIONS 2026-05-18), so it must
    be able to create passcode-null ``Card`` / ``CardPrinting`` rows, not merely
    price existing ones. ``PriceData`` alone (an id + prices) can't create those
    printings. The role split permits the fix — one TCGCSV adapter can implement
    ``MetadataProvider`` too — but ``CardMetadata`` must first gain a
    passcode-optional form (see its docstring). Until then this foundation only
    covers passcode-bearing YGOPRODeck cards, which is correct for this slice.
    """

    @abc.abstractmethod
    def fetch_prices(self) -> Iterable[PriceData]:
        """Yield every price row the provider currently reports."""
