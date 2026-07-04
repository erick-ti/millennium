import pytest

from apps.imports.dragon_shield import (
    ImportParseError,
    normalize_row,
    parse_dragon_shield,
)

_HEADER = (
    "Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,"
    "Rarity,Condition,Printing,Language,Price Bought,Date Bought,LOW,MID,MARKET"
)
_ASH_ROW = (
    "Yubel Deck,3,0,Ash Blossom & Joyous Spring,L5DD,\"Legendary 5D's Decks\","
    "L5DD-ENC09,C,NearMint,1st Edition,English,0.68,2024-01-15,0.50,0.60,0.68"
)
_ONE_FOR_ONE_ALT_ROW = (
    "Yubel Deck,1,0,One for One,RA03,Quarter Century Stampede,"
    "RA03-EN056alt,PlScR,NearMint,1st Edition,English,1.46,2024-02-01,1.20,1.35,1.46"
)
# Real DS exports quote the Excel sep hint ('"sep=,"', verified against the sample),
# so the fixtures use that form. The bare 'sep=,' is covered separately below.
_SAMPLE = f'"sep=,"\n{_HEADER}\n{_ASH_ROW}\n{_ONE_FOR_ONE_ALT_ROW}\n'


# --- parser ----------------------------------------------------------------


def test_parse_skips_sep_hint_and_reads_rows() -> None:
    rows = parse_dragon_shield(_SAMPLE)

    assert [r.row_number for r in rows] == [1, 2]
    first = rows[0].raw
    assert first["Card Name"] == "Ash Blossom & Joyous Spring"
    assert first["Card Number"] == "L5DD-ENC09"
    # The full row is preserved verbatim, including columns the importer ignores.
    assert first["Trade Quantity"] == "0"
    assert first["MARKET"] == "0.68"


def test_parse_handles_quoted_comma_in_set_name() -> None:
    csv_text = (
        f'"sep=,"\n{_HEADER}\n'
        'Binder,1,0,Some Card,XYZ,"Legendary Decks, Vol. 2",'
        "XYZ-EN001,C,NearMint,1st Edition,English,1.00,2024-01-01,,,"
    )

    rows = parse_dragon_shield(csv_text)

    assert rows[0].raw["Set Name"] == "Legendary Decks, Vol. 2"


@pytest.mark.parametrize("sep_line", ['"sep=,"', "sep=,"])
def test_parse_accepts_quoted_and_unquoted_sep_hint(sep_line: str) -> None:
    """Real DS exports quote the hint ('"sep=,"'); some tools emit it bare. Both must
    be dropped, not read as the header: matching only the bare form batch-failed a
    valid export."""
    rows = parse_dragon_shield(f"{sep_line}\n{_HEADER}\n{_ASH_ROW}\n")

    assert len(rows) == 1
    assert rows[0].raw["Card Name"] == "Ash Blossom & Joyous Spring"


def test_parse_handles_crlf_line_endings() -> None:
    crlf = _SAMPLE.replace("\n", "\r\n")

    rows = parse_dragon_shield(crlf)

    assert len(rows) == 2
    assert rows[0].raw["Card Number"] == "L5DD-ENC09"


def test_parse_without_sep_hint() -> None:
    rows = parse_dragon_shield(f"{_HEADER}\n{_ASH_ROW}\n")

    assert len(rows) == 1
    assert rows[0].raw["Card Name"] == "Ash Blossom & Joyous Spring"


def test_parse_rejects_non_dragon_shield_header() -> None:
    with pytest.raises(ImportParseError):
        parse_dragon_shield("col_a,col_b\n1,2\n")


def test_parse_short_row_fills_missing_fields_with_blank() -> None:
    """A truncated row (fewer values than the header) leaves later columns blank
    rather than raising: normalization decides whether the gaps matter."""
    rows = parse_dragon_shield(f"{_HEADER}\nYubel Deck,3,0,Ash Blossom\n")

    assert rows[0].raw["Card Name"] == "Ash Blossom"
    assert rows[0].raw["Date Bought"] == ""


@pytest.mark.parametrize("prefix", ["\ufeff", ""])
def test_parse_strips_leading_bom_before_sep_hint(prefix: str) -> None:
    """Excel "CSV UTF-8" saves prepend a BOM, and str.strip() doesn't treat it as
    whitespace, so an unstripped BOM before the sep hint would batch-fail a valid
    file."""
    rows = parse_dragon_shield(f'{prefix}"sep=,"\n{_HEADER}\n{_ASH_ROW}\n')

    assert len(rows) == 1
    assert rows[0].raw["Card Name"] == "Ash Blossom & Joyous Spring"


def test_parse_strips_leading_bom_before_header() -> None:
    """A BOM directly before the header (no sep hint) must not make Folder Name look
    like a missing required column."""
    rows = parse_dragon_shield(f"\ufeff{_HEADER}\n{_ASH_ROW}\n")

    assert len(rows) == 1
    assert rows[0].raw["Folder Name"] == "Yubel Deck"


# --- normalizer ------------------------------------------------------------


def _ash() -> dict[str, str]:
    return parse_dragon_shield(_SAMPLE)[0].raw


def _one_for_one_alt() -> dict[str, str]:
    return parse_dragon_shield(_SAMPLE)[1].raw


def test_normalize_clean_row() -> None:
    result = normalize_row(_ash())

    assert result.issues == ()
    assert result.data == {
        "portfolio_name": "Yubel Deck",
        "card_name": "Ash Blossom & Joyous Spring",
        "set_code": "L5DD-ENC09",
        "set_rarity": "Common",
        "variant_label": None,
        "edition": "first",
        "condition": "near_mint",
        "language": "en",
        "quantity": 3,
        "unit_cost": "0.68",
        "acquired_at": "2024-01-15",
    }


def test_normalize_strips_alt_suffix_into_variant_label() -> None:
    result = normalize_row(_one_for_one_alt())

    assert result.issues == ()
    assert result.data["set_code"] == "RA03-EN056"
    assert result.data["variant_label"] == "alt art"
    assert result.data["set_rarity"] == "Platinum Secret Rare"


@pytest.mark.parametrize(
    "ds_code, expected",
    [
        ("C", "Common"),
        ("UR", "Ultra Rare"),
        ("ScR", "Secret Rare"),
        ("PlScR", "Platinum Secret Rare"),
        ("QCScR", "Quarter Century Secret Rare"),
    ],
)
def test_normalize_maps_rarity_codes(ds_code: str, expected: str) -> None:
    raw = _ash()
    raw["Rarity"] = ds_code

    assert normalize_row(raw).data["set_rarity"] == expected


def test_normalize_unmapped_rarity_is_null_with_issue() -> None:
    raw = _ash()
    raw["Rarity"] = "GUR"  # Gold Ultra Rare, not in the v1 table

    result = normalize_row(raw)

    assert result.data["set_rarity"] is None
    assert any("rarity" in issue and "GUR" in issue for issue in result.issues)


@pytest.mark.parametrize(
    "column, bad_value, field, label",
    [
        ("Condition", "Pristine", "condition", "condition"),
        ("Printing", "First Edition", "edition", "printing"),
        ("Language", "Klingon", "language", "language"),
    ],
)
def test_normalize_unmapped_closed_vocab_is_null_with_issue(
    column: str, bad_value: str, field: str, label: str
) -> None:
    raw = _ash()
    raw[column] = bad_value

    result = normalize_row(raw)

    assert result.data[field] is None
    assert any(label in issue and bad_value in issue for issue in result.issues)


def test_normalize_blank_cost_and_date_are_not_issues() -> None:
    """An unknown cost/date is the normal state for gifts/trades/legacy lots, so a
    blank one normalizes to None without flagging the row."""
    raw = _ash()
    raw["Price Bought"] = ""
    raw["Date Bought"] = ""

    result = normalize_row(raw)

    assert result.data["unit_cost"] is None
    assert result.data["acquired_at"] is None
    assert result.issues == ()


@pytest.mark.parametrize("bad_quantity", ["abc", "0", "-2", "", "2147483648"])
def test_normalize_invalid_quantity_is_null_with_issue(bad_quantity: str) -> None:
    raw = _ash()
    raw["Quantity"] = bad_quantity

    result = normalize_row(raw)

    assert result.data["quantity"] is None
    assert any("quantity" in issue for issue in result.issues)


@pytest.mark.parametrize("bad_price", ["free", "-1.00", "NaN", "0.001", "10000000000.00"])
def test_normalize_invalid_price_is_null_with_issue(bad_price: str) -> None:
    """Format-invalid (free/NaN/negative) and contract-invalid (>2 dp would silently
    round; over Decimal(12, 2) max would error at materialize) prices all flag rather
    than passing as clean."""
    raw = _ash()
    raw["Price Bought"] = bad_price

    result = normalize_row(raw)

    assert result.data["unit_cost"] is None
    assert any("price" in issue for issue in result.issues)


@pytest.mark.parametrize(
    "price, expected",
    [("0.68", "0.68"), ("5", "5"), ("0.5", "0.5"), ("9999999999.99", "9999999999.99")],
)
def test_normalize_accepts_in_contract_prices(price: str, expected: str) -> None:
    """0/1/2-dp prices and the Decimal(12, 2) maximum fit the downstream contract, so
    they normalize clean: no silent rounding, no spurious issue."""
    raw = _ash()
    raw["Price Bought"] = price

    result = normalize_row(raw)

    assert result.data["unit_cost"] == expected
    assert result.issues == ()


def test_normalize_accepts_max_quantity() -> None:
    """The PositiveIntegerField upper bound is in-contract; only values above it flag."""
    raw = _ash()
    raw["Quantity"] = "2147483647"

    result = normalize_row(raw)

    assert result.data["quantity"] == 2147483647
    assert result.issues == ()


def test_normalize_invalid_date_is_null_with_issue() -> None:
    raw = _ash()
    raw["Date Bought"] = "01/15/2024"  # not ISO 8601

    result = normalize_row(raw)

    assert result.data["acquired_at"] is None
    assert any("date" in issue for issue in result.issues)


def test_normalize_empty_required_text_flags_issues() -> None:
    raw = _ash()
    raw["Folder Name"] = "  "
    raw["Card Name"] = ""

    result = normalize_row(raw)

    assert result.data["portfolio_name"] is None
    assert result.data["card_name"] is None
    assert any("folder name" in issue for issue in result.issues)
    assert any("card name" in issue for issue in result.issues)
