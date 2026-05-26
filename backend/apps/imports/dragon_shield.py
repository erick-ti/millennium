from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from apps.collection.models import Condition, Language
from apps.core.enums import Edition

# --- Dragon Shield CSV format (recon PHASE_1A5_FINDINGS Q1/Q2/Q5) -----------

# The columns this importer reads. Their presence is how a file is recognized as a
# DS export; extra / reordered columns are tolerated. (DS exports more — Trade
# Quantity, Set Code, Set Name, LOW/MID/MARKET — but the matcher / materializer
# don't need them; the full raw row is preserved on ImportRow.raw_data regardless.)
_REQUIRED_COLUMNS = frozenset(
    {
        "Folder Name",
        "Quantity",
        "Card Name",
        "Card Number",
        "Rarity",
        "Condition",
        "Printing",
        "Language",
        "Price Bought",
        "Date Bought",
    }
)

# Excel prepends a delimiter-hint line so it knows the separator; it isn't data and is
# dropped before the header (recon Q1). Dragon Shield *quotes* it ('"sep=,"', verified
# against the real sample); some tools emit it bare ('sep=,'). `_is_sep_hint` handles both.
_SEP_HINT_PREFIX = "sep="

# DS appends alt-art onto the Card Number as a trailing "alt" (a DS-only convention,
# absent from YGOPRODeck / TCGCSV); strip it for the set_code and record the variant
# (recon Q2/Q4). Lowercase, as DS emits it — card numbers otherwise end in digits, so
# this can't false-strip a real code.
_ALT_SUFFIX = "alt"
_ALT_VARIANT_LABEL = "alt art"

# DS rarity shorthand -> YGOPRODeck `set_rarity` name. This is the *provisional*
# rarity the matcher resolves a printing against (alias-aware: TCGCSV may have
# reconciled it to e.g. "Prismatic Ultimate Rare" in place, recording a
# PrintingAlias on this original value — DECISIONS 2026-05-23/24). The recon Q5
# sample set: a v1 table to expand against real data — an unmapped code is flagged
# (-> review), never guessed into a wrong match.
_RARITY_BY_DS_CODE = {
    "C": "Common",
    "R": "Rare",
    "SR": "Super Rare",
    "UR": "Ultra Rare",
    "ScR": "Secret Rare",
    "UtR": "Ultimate Rare",
    "StR": "Starlight Rare",
    "PScR": "Prismatic Secret Rare",
    "PGR": "Premium Gold Rare",
    "PlScR": "Platinum Secret Rare",
    "QCScR": "Quarter Century Secret Rare",
}

# DS `Printing` -> Edition slug; `Condition` (no-space PascalCase) -> Condition slug;
# `Language` (full English name) -> Language ISO code. Keyed off the enums so the
# stored slug stays correct if an enum value ever changes.
_EDITION_BY_DS_PRINTING = {
    "1st Edition": Edition.FIRST_EDITION.value,
    "Unlimited": Edition.UNLIMITED.value,
    "Limited": Edition.LIMITED.value,
}
_CONDITION_BY_DS = {
    "Mint": Condition.MINT.value,
    "NearMint": Condition.NEAR_MINT.value,
    "Excellent": Condition.EXCELLENT.value,
    "Good": Condition.GOOD.value,
    "LightPlayed": Condition.LIGHT_PLAYED.value,
    "Played": Condition.PLAYED.value,
    "Poor": Condition.POOR.value,
}
_LANGUAGE_BY_DS = {
    "English": Language.ENGLISH.value,
    "French": Language.FRENCH.value,
    "German": Language.GERMAN.value,
    "Italian": Language.ITALIAN.value,
    "Spanish": Language.SPANISH.value,
    "Portuguese": Language.PORTUGUESE.value,
    "Japanese": Language.JAPANESE.value,
    "Korean": Language.KOREAN.value,
}

# Downstream-contract limits a normalized value must satisfy to materialize without
# silent rounding or a late DB error, so "no issues" means "materializable", not just
# "well-formed" (Codex review 2026-05-26). These mirror the stable CollectionLot field
# params (DecimalField(max_digits=12, decimal_places=2); PositiveIntegerField), kept as
# constants rather than model introspection for mypy-clean simplicity; a value that
# violates them is flagged (-> review), never clamped/rounded.
_MAX_UNIT_COST = Decimal("9999999999.99")  # largest value Decimal(12, 2) holds
_COST_QUANTUM = Decimal("0.01")  # decimal_places=2
_MAX_QUANTITY = 2_147_483_647  # PositiveIntegerField upper bound (safe on every backend)


class ImportParseError(ValueError):
    """The uploaded file isn't a recognizable Dragon Shield CSV (a missing / wrong
    header). Raised by ``parse_dragon_shield`` so the orchestration records a
    batch-level FAILED rather than persisting unusable rows. Distinct from a per-row
    normalization issue, which is recorded on the row, not raised."""


@dataclass(frozen=True, slots=True)
class ParsedRow:
    """One CSV data row as parsed: its 1-based source position and the verbatim
    header->value mapping (stored as ``ImportRow.raw_data``)."""

    row_number: int
    raw: dict[str, str]


@dataclass(frozen=True, slots=True)
class NormalizedRow:
    """A raw DS row mapped to canonical fields (stored as ``ImportRow.normalized_data``)
    plus any normalization ``issues``.

    ``data`` holds JSON-native values only (str / int / None) so it round-trips
    through the ``JSONField``: Decimal cost and the date are kept as strings and
    parsed back when a lot is materialized. A value that can't be mapped is left
    ``None`` with an entry in ``issues`` rather than aborting — the row is staging a
    human will triage. ``issues`` empty means a clean row; the orchestration turns a
    non-empty list into the row's error / needs-review state (slice 4).
    """

    data: dict[str, Any]
    issues: tuple[str, ...]


def parse_dragon_shield(content: str) -> list[ParsedRow]:
    """Parse Dragon Shield CSV text into raw rows.

    Drops Excel's leading ``sep=,`` hint line if present, then reads via
    ``csv.DictReader`` so quoted commas (DS ``Set Name`` contains them) parse
    correctly. Raises ``ImportParseError`` if the header lacks the DS columns. Does
    NOT normalize (that's ``normalize_row``), so a row is preserved verbatim even if
    it later fails normalization — the basis for re-normalizing when logic improves.
    """
    # Strip a leading UTF-8 BOM: str.strip() doesn't treat a BOM (U+FEFF) as whitespace, so a
    # BOM (which Excel adds to "CSV UTF-8" saves) before the sep hint / header would
    # otherwise be read as data and batch-fail the file. Belt-and-suspenders with
    # slice 4 decoding the upload as utf-8-sig (Codex review 2026-05-26).
    content = content.removeprefix("\ufeff")
    lines = content.splitlines()
    if lines and _is_sep_hint(lines[0]):
        lines = lines[1:]
    reader = csv.DictReader(lines)
    fieldnames = reader.fieldnames
    if fieldnames is None or not _REQUIRED_COLUMNS.issubset(fieldnames):
        missing = sorted(_REQUIRED_COLUMNS.difference(fieldnames or []))
        raise ImportParseError(f"not a Dragon Shield export — missing columns: {missing}")
    rows: list[ParsedRow] = []
    for i, record in enumerate(reader, start=1):
        # DictReader values are str; coerce defensively. A short row fills missing
        # fields with restval (None); a long row buckets extras under the None
        # restkey (dropped here).
        cleaned: dict[str, str] = {}
        for key, value in record.items():
            if key is None:
                continue
            cleaned[key] = "" if value is None else str(value)
        rows.append(ParsedRow(row_number=i, raw=cleaned))
    return rows


def normalize_row(raw: dict[str, str]) -> NormalizedRow:
    """Map a raw DS row to canonical ``normalized_data`` + a list of issues.

    Each field is mapped independently; an unmappable closed-vocabulary value (an
    unknown rarity code, an out-of-vocabulary condition) or an unparseable
    quantity / price / date is left ``None`` and recorded in ``issues`` instead of
    aborting the row. Identity-bearing fields (folder, card name, set_code, rarity,
    edition, condition, language, a positive quantity) record an issue when absent;
    an absent cost or date is the normal "unknown" state (gifts, trades, legacy
    hand-entry) and is NOT an issue. ``set_code`` / ``set_rarity`` are trimmed at this
    boundary (the deferred canonicalization obligation, DECISIONS 2026-05-21).
    """
    issues: list[str] = []

    portfolio_name = _required_text(raw.get("Folder Name", ""), "folder name", issues)
    card_name = _required_text(raw.get("Card Name", ""), "card name", issues)

    set_code, variant_label = _split_card_number(raw.get("Card Number", ""))
    if not set_code:
        issues.append("missing or empty card number")

    set_rarity = _map_closed(raw.get("Rarity", ""), _RARITY_BY_DS_CODE, "rarity", issues)
    edition = _map_closed(raw.get("Printing", ""), _EDITION_BY_DS_PRINTING, "printing", issues)
    condition = _map_closed(raw.get("Condition", ""), _CONDITION_BY_DS, "condition", issues)
    language = _map_closed(raw.get("Language", ""), _LANGUAGE_BY_DS, "language", issues)

    quantity = _parse_quantity(raw.get("Quantity", ""), issues)
    unit_cost = _parse_price(raw.get("Price Bought", ""), issues)
    acquired_at = _parse_date(raw.get("Date Bought", ""), issues)

    data: dict[str, Any] = {
        "portfolio_name": portfolio_name,
        "card_name": card_name,
        "set_code": set_code or None,
        "set_rarity": set_rarity,
        "variant_label": variant_label,
        "edition": edition,
        "condition": condition,
        "language": language,
        "quantity": quantity,
        "unit_cost": unit_cost,
        "acquired_at": acquired_at,
    }
    return NormalizedRow(data=data, issues=tuple(issues))


def _required_text(value: str, label: str, issues: list[str]) -> str | None:
    text = value.strip()
    if not text:
        issues.append(f"missing or empty {label}")
        return None
    return text


def _map_closed(value: str, mapping: dict[str, str], label: str, issues: list[str]) -> str | None:
    text = value.strip()
    if not text:
        issues.append(f"missing or empty {label}")
        return None
    mapped = mapping.get(text)
    if mapped is None:
        issues.append(f"unmapped {label} {text!r}")
        return None
    return mapped


def _split_card_number(card_number: str) -> tuple[str, str | None]:
    code = card_number.strip()
    if code.endswith(_ALT_SUFFIX) and len(code) > len(_ALT_SUFFIX):
        return code[: -len(_ALT_SUFFIX)], _ALT_VARIANT_LABEL
    return code, None


def _parse_quantity(value: str, issues: list[str]) -> int | None:
    text = value.strip()
    if not text:
        issues.append("missing quantity")
        return None
    try:
        quantity = int(text)
    except ValueError:
        issues.append(f"invalid quantity {text!r}")
        return None
    if quantity < 1:
        issues.append(f"non-positive quantity {quantity}")
        return None
    if quantity > _MAX_QUANTITY:
        # Above PositiveIntegerField's range — would error at materialize, not round.
        issues.append(f"quantity {quantity} exceeds the maximum {_MAX_QUANTITY}")
        return None
    return quantity


def _parse_price(value: str, issues: list[str]) -> str | None:
    text = value.strip()
    if not text:
        return None  # unknown cost is allowed (gift / trade / legacy), not an issue
    try:
        amount = Decimal(text)
    except InvalidOperation:
        issues.append(f"invalid price {text!r}")
        return None
    if not amount.is_finite() or amount < 0:
        issues.append(f"invalid price {text!r}")
        return None
    # Must fit CollectionLot.unit_cost = Decimal(12, 2) *exactly*: an over-max magnitude
    # would error at materialize, and >2 decimal places would silently round on save
    # (corrupting cost basis). Magnitude is checked first so quantize only runs on
    # in-range values (never exceeding the Decimal context precision). Reject, not round.
    if amount > _MAX_UNIT_COST:
        issues.append(f"price {text!r} exceeds the maximum {_MAX_UNIT_COST}")
        return None
    if amount.quantize(_COST_QUANTUM) != amount:
        issues.append(f"price {text!r} has more than 2 decimal places")
        return None
    # Stored as a string so the exact decimal survives JSON (no binary-float noise);
    # parsed back to Decimal for CollectionLot.unit_cost (12, 2) at materialize.
    return str(amount)


def _parse_date(value: str, issues: list[str]) -> str | None:
    text = value.strip()
    if not text:
        return None  # unknown acquisition date is allowed, not an issue
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        issues.append(f"invalid date {text!r}")
        return None
    return parsed.isoformat()


def _is_sep_hint(line: str) -> bool:
    # The hint line is quoted in real DS exports ('"sep=,"') and bare in some tools
    # ('sep=,'); strip surrounding quotes/whitespace before the check so a quoted hint
    # isn't read as the header and used to batch-fail a valid export (Codex review,
    # 2026-05-26 — the original raw-line check matched only the bare form).
    candidate = line.strip().strip('"').strip().lower().replace(" ", "")
    return candidate.startswith(_SEP_HINT_PREFIX)
