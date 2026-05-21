from __future__ import annotations

import html
import re
import unicodedata

# Fold curly quotes and en/em dashes to ASCII before matching. Keyed by code
# point to avoid ambiguous-unicode literals in source.
_PUNCT_MAP = {
    0x2018: "'",  # left single quote
    0x2019: "'",  # right single quote
    0x201C: '"',  # left double quote
    0x201D: '"',  # right double quote
    0x2013: "-",  # en dash
    0x2014: "-",  # em dash
}


def normalize_name(name: str) -> str:
    """Canonical search key for a card name.

    Single source of truth for ``Card.normalized_name`` (applied in
    ``Card.save``): decode HTML entities, fold curly quotes/dashes to ASCII,
    strip accents, collapse whitespace, lowercase.
    """
    decoded = html.unescape(name).translate(_PUNCT_MAP)
    decomposed = unicodedata.normalize("NFKD", decoded)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents).strip().lower()
