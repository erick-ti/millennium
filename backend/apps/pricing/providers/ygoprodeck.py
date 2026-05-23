from __future__ import annotations

import html
from typing import Any

import structlog

from apps.pricing.providers.base import (
    CardMetadata,
    JsonFetcher,
    MetadataProvider,
    PrintingMetadata,
    fetch_json,
)

logger = structlog.get_logger(__name__)

# One request returns the full card list. YGOPRODeck's own prices (`card_prices`)
# are the cheapest across all versions and useless for a specific printing, so
# this provider is metadata only — pricing comes from TCGCSV (the source split).
CARDINFO_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"

# Coarse sanity floor, not a precise expectation: the full dump is ~14k cards
# and only grows (Konami never un-releases), so any value well under that never
# false-rejects but catches a grossly truncated response (a cut connection
# yields a handful of cards). The precise guard — comparing against the last
# successful sync — needs run history and lands with slice 4's Celery wiring
# (DECISIONS 2026-05-23 round-4 follow-up).
_MIN_EXPECTED_CARDS = 1000


class YgoprodeckProvider(MetadataProvider):
    """Card metadata from YGOPRODeck's ``cardinfo.php`` bulk dump."""

    def __init__(
        self, fetch: JsonFetcher = fetch_json, *, min_cards: int = _MIN_EXPECTED_CARDS
    ) -> None:
        self._fetch = fetch
        self._min_cards = min_cards

    def fetch_card_metadata(self) -> list[CardMetadata]:
        payload = self._fetch(CARDINFO_URL)
        data = payload.get("data") if isinstance(payload, dict) else None
        # Fail closed: the full dump is always a non-empty list. A missing/
        # non-list `data` or zero usable cards is an upstream/API-shape failure
        # (e.g. a 200 carrying {"error": ...}), not a valid empty catalog —
        # raise so it can't masquerade as a successful zero-row sync.
        if not isinstance(data, list):
            raise ValueError(
                "YGOPRODeck response has no 'data' list — refusing to treat an "
                "upstream failure as an empty catalog."
            )
        cards: list[CardMetadata] = []
        skipped_invalid_rarity = 0
        for raw in data:
            card, skipped = _normalize_card(raw)
            skipped_invalid_rarity += skipped
            if card is not None:
                cards.append(card)
        if not cards:
            raise ValueError(
                "YGOPRODeck returned zero usable cards — refusing to treat an "
                "upstream failure as an empty catalog."
            )
        if len(cards) < self._min_cards:
            raise ValueError(
                f"YGOPRODeck returned {len(cards)} usable cards, below the sanity "
                f"floor of {self._min_cards} — refusing a likely-truncated bulk "
                f"dump (the full dump is ~14k cards)."
            )
        if skipped_invalid_rarity:
            # Surfaced (not silently dropped) so upstream data-quality drift is
            # visible in the command run and the daily Celery logs.
            logger.warning("ygoprodeck_sync.skipped_invalid_rarity", count=skipped_invalid_rarity)
        return cards


def _normalize_card(raw: dict[str, Any]) -> tuple[CardMetadata | None, int]:
    """Return (card, count_of_printings_skipped_for_invalid_rarity)."""
    passcode = raw.get("id")
    name = raw.get("name")
    if passcode is None or not name:
        # A card without a Konami passcode or name can't form an identity here.
        # (Passcode-less TCGCSV-only entities such as Tokens arrive via pricing.)
        return None, 0
    printings, skipped = _normalize_printings(raw.get("card_sets") or [])
    return (
        CardMetadata(
            # YGOPRODeck serves HTML-encoded names (e.g. "The Fallen &amp; The
            # Virtuous"); decode before storing so Card.name is clean display text
            # rather than polluted with entities. unescape-then-strip handles an
            # entity that decodes to whitespace; idempotent with normalize_name's
            # own unescape on the derived normalized_name.
            passcode=int(passcode),
            name=html.unescape(str(name)).strip(),
            printings=printings,
        ),
        skipped,
    )


def _normalize_printings(
    raw_sets: list[dict[str, Any]],
) -> tuple[tuple[PrintingMetadata, ...], int]:
    # Keyed by (set_code, set_rarity) to drop duplicates: YGOPRODeck lists
    # alt-arts as repeated rows with no distinguishing label, and we can't
    # disambiguate them here (variant_label stays NULL), so keep the first —
    # per-art splitting is a Phase 3 import-matching concern.
    seen: dict[tuple[str, str], PrintingMetadata] = {}
    skipped = 0
    for entry in raw_sets:
        set_code = str(entry.get("set_code", "")).strip()
        set_rarity = str(entry.get("set_rarity", "")).strip()
        if not set_code:
            # No set to identify — structural, can't form a printing key.
            # (Defensive trim/skip: this sync is the controlled writer the
            # set_code canonicalization was deferred to — DECISIONS 2026-05-21.)
            continue
        if not _is_plausible_rarity(set_rarity):
            # Drop only *blatant* garbage (blank/numeric, e.g. L5DD-ENC09 "2").
            # This is NOT rarity validation: the systematic YGOPRODeck-vs-TCGCSV
            # disagreements (Prismatic / "New artwork" / "Short Print") look like
            # valid names and pass through here as provisional rarities — TCGCSV
            # is canonical and reconciles them in the ingestion slice (DECISIONS
            # 2026-05-23). "Flag, don't crash" — skip and count, don't abort.
            skipped += 1
            continue
        key = (set_code, set_rarity)
        if key not in seen:
            seen[key] = PrintingMetadata(
                set_code=set_code,
                set_rarity=set_rarity,
                # set_name is display prose like the card name — decode entities.
                # set_code/set_rarity are identifiers (natural-key components),
                # left as trimmed-only to avoid altering a key.
                set_name=html.unescape(str(entry.get("set_name", ""))).strip(),
                variant_label=None,
            )
    return tuple(seen.values()), skipped


def _is_plausible_rarity(set_rarity: str) -> bool:
    # Blatant-garbage guard only, NOT canonical-rarity validation: a real
    # Yu-Gi-Oh rarity is a name ("Common", "Secret Rare", ...), never blank or
    # purely numeric, so reject "2"/"3" (the documented YGOPRODeck bug). Valid-
    # looking but non-canonical rarities (Prismatic / "New artwork" / "Short
    # Print") deliberately pass — TCGCSV reconciles those (DECISIONS 2026-05-23).
    return bool(set_rarity) and not set_rarity.isdigit()
