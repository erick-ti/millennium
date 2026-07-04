from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.cards.models import Card, CardPrinting
from apps.collection.models import CollectionItem, CollectionLot, Condition, Language
from apps.core.enums import Edition
from apps.core.models import SyncKind, SyncStatus
from apps.core.sync_history import record_run
from apps.imports.matching import MatchResult
from apps.imports.matching import match_row as real_match_row
from apps.imports.models import ImportBatch, ImportRow, ImportStatus, MatchConfidence, RowStatus
from apps.imports.sync import run_import
from apps.portfolio.models import Portfolio

# --- fixtures: a Dragon Shield CSV + a catalog printing the ash row matches -----

_HEADER = (
    "Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,"
    "Rarity,Condition,Printing,Language,Price Bought,Date Bought,LOW,MID,MARKET"
)


def _row(
    *,
    folder: str = "Yubel Deck",
    quantity: str = "3",
    card_name: str = "Ash Blossom & Joyous Spring",
    card_number: str = "L5DD-ENC09",
    rarity: str = "C",
    condition: str = "NearMint",
    printing: str = "1st Edition",
    language: str = "English",
    price: str = "0.68",
    acquired: str = "2024-01-15",
) -> str:
    """One DS data row. Defaults map to: set_code L5DD-ENC09, Common, 1st Edition,
    Near Mint, English, qty 3, $0.68, 2024-01-15, i.e. the ``_printing`` below."""
    set_code_col = card_number.split("-")[0]  # the bare prefix DS puts in Set Code (ignored)
    return (
        f"{folder},{quantity},0,{card_name},{set_code_col},\"Some Set\",{card_number},"
        f"{rarity},{condition},{printing},{language},{price},{acquired},,,"
    )


def _csv(*rows: str) -> str:
    return "\n".join(['"sep=,"', _HEADER, *rows]) + "\n"


def _printing(
    *,
    name: str = "Ash Blossom & Joyous Spring",
    set_code: str = "L5DD-ENC09",
    set_rarity: str = "Common",
    is_multi_variant: bool = False,
) -> CardPrinting:
    card = Card.objects.create(name=name)
    return CardPrinting.objects.create(
        card=card,
        set_code=set_code,
        set_rarity=set_rarity,
        set_name="Some Set",
        is_multi_variant=is_multi_variant,
    )


def _record_reconciliation() -> None:
    """Record today's successful TCGCSV pricing/reconcile run: the materialization gate."""
    record_run(SyncKind.TCGCSV_PRICING, SyncStatus.SUCCESS, product_count=1, price_row_count=1)


# --- run_import: the materialization decision table -----------------------------


@pytest.mark.django_db
def test_materializes_exact_with_fresh_reconciliation() -> None:
    printing = _printing()
    _record_reconciliation()

    result = run_import(_csv(_row()), filename="ds.csv")

    assert (result.rows_total, result.rows_materialized, result.rows_pending_review) == (1, 1, 0)
    assert result.status == ImportStatus.COMPLETED.value
    assert result.materialization_allowed is True

    portfolio = Portfolio.objects.get(name="Yubel Deck")
    item = CollectionItem.objects.get(printing=printing, portfolio=portfolio)
    assert item.condition == Condition.NEAR_MINT
    assert item.edition == Edition.FIRST_EDITION
    assert item.language == Language.ENGLISH
    lot = item.lots.get()
    assert (lot.quantity, lot.unit_cost, lot.acquired_at) == (3, Decimal("0.68"), date(2024, 1, 15))
    assert lot.import_source_ref == f"dragon_shield:item:{item.pk}"

    row = ImportRow.objects.get(batch_id=result.batch_id)
    assert row.status == RowStatus.MATERIALIZED
    assert row.match_confidence == MatchConfidence.EXACT


@pytest.mark.django_db
def test_exact_staged_pending_when_reconciliation_is_stale() -> None:
    """An EXACT match must NOT auto-materialize unless a fresh
    successful TCGCSV reconciliation exists (else the multi-variant guard may be stale).
    The row stages PENDING and nothing touches the collection, not even the folder's
    portfolio is created."""
    _printing()
    # no reconciliation recorded

    result = run_import(_csv(_row()), filename="ds.csv")

    assert (result.rows_materialized, result.rows_pending_review) == (0, 1)
    assert result.status == ImportStatus.REVIEW.value
    assert result.materialization_allowed is False
    assert CollectionItem.objects.count() == 0
    assert CollectionLot.objects.count() == 0
    assert Portfolio.objects.count() == 0

    row = ImportRow.objects.get(batch_id=result.batch_id)
    assert row.status == RowStatus.PENDING
    assert row.match_confidence == MatchConfidence.EXACT
    assert "reconciliation" in row.error_message.lower()


@pytest.mark.django_db
def test_exact_staged_when_printing_created_after_reconciliation() -> None:
    """Per-printing coverage gate: a printing created AFTER the day's reconciliation was
    never multi-variant-checked (is_multi_variant defaults False), so an EXACT match on it
    stages rather than auto-materializing, even though a same-day reconciliation exists.
    The check is the printing's own created_at vs the reconciliation time (not a once-per-
    batch flag or metadata-SyncRun ordering), which closes the hole a batch-global gate
    would leave open for a printing created by a concurrent or partially-failed metadata sync."""
    # Reconciliation first; the matched printing is created AFTER it (e.g. a later/concurrent
    # metadata sync), so it post-dates the cutoff and is uncovered.
    record_run(SyncKind.TCGCSV_PRICING, SyncStatus.SUCCESS, product_count=1, price_row_count=1)
    _printing()

    result = run_import(_csv(_row()), filename="ds.csv")

    assert result.rows_materialized == 0
    assert result.rows_pending_review == 1
    # A reconciliation DID run today (so the batch-level flag is True), but THIS printing
    # post-dates it, so the per-printing check is what stages the row.
    assert result.materialization_allowed is True
    assert CollectionItem.objects.count() == 0
    row = ImportRow.objects.get(batch_id=result.batch_id)
    assert row.status == RowStatus.PENDING
    assert row.match_confidence == MatchConfidence.EXACT
    assert "not yet covered" in row.error_message


@pytest.mark.django_db
def test_covered_printing_materializes_despite_a_later_metadata_sync() -> None:
    """The gate is per-printing, not metadata-sync ordering: a printing created BEFORE the
    day's reconciliation is covered and materializes even if an unrelated metadata sync ran
    afterward (a coarser ordering gate would have wrongly staged it). Only printings such a
    later sync *created* are uncovered, and they stage individually."""
    _printing()  # created before the reconciliation -> covered
    record_run(SyncKind.TCGCSV_PRICING, SyncStatus.SUCCESS, product_count=1, price_row_count=1)
    # A later metadata sync runs but did not create the matched printing.
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=1, printing_count=1)

    result = run_import(_csv(_row()), filename="ds.csv")

    assert result.rows_materialized == 1
    assert result.materialization_allowed is True


@pytest.mark.django_db
def test_medium_match_routes_to_review_even_when_fresh() -> None:
    """A printing exists for the key but its card name disagrees -> MEDIUM -> review,
    never materialized, even with a fresh reconciliation. The best candidate is kept."""
    _printing(name="A Totally Different Card")
    _record_reconciliation()

    result = run_import(_csv(_row()), filename="ds.csv")

    assert (result.rows_materialized, result.rows_pending_review) == (0, 1)
    assert CollectionItem.objects.count() == 0
    row = ImportRow.objects.get(batch_id=result.batch_id)
    assert row.status == RowStatus.PENDING
    assert row.match_confidence == MatchConfidence.MEDIUM
    assert row.matched_printing is not None


@pytest.mark.django_db
def test_multi_variant_printing_routes_to_review() -> None:
    """A known multi-variant placeholder is downgraded to MEDIUM by the matcher even
    when the name agrees, so run_import stages it for review rather than materializing
    an ambiguous holding: the whole point of the is_multi_variant flag flowing through."""
    _printing(is_multi_variant=True)
    _record_reconciliation()

    result = run_import(_csv(_row()), filename="ds.csv")

    assert result.rows_materialized == 0
    assert result.rows_pending_review == 1
    assert CollectionItem.objects.count() == 0
    row = ImportRow.objects.get(batch_id=result.batch_id)
    assert row.match_confidence == MatchConfidence.MEDIUM


@pytest.mark.django_db
def test_unmatched_routes_to_review() -> None:
    _record_reconciliation()  # no printing in the catalog

    result = run_import(_csv(_row()), filename="ds.csv")

    assert (result.rows_materialized, result.rows_pending_review) == (0, 1)
    row = ImportRow.objects.get(batch_id=result.batch_id)
    assert row.status == RowStatus.PENDING
    assert row.match_confidence == MatchConfidence.UNMATCHED
    assert row.matched_printing is None


@pytest.mark.django_db
def test_normalization_issue_is_error_and_not_matched() -> None:
    _printing()
    _record_reconciliation()

    result = run_import(_csv(_row(rarity="ZZ")), filename="ds.csv")  # unmapped rarity -> issue

    assert (result.rows_error, result.rows_materialized) == (1, 0)
    assert result.status == ImportStatus.REVIEW.value
    assert CollectionItem.objects.count() == 0
    row = ImportRow.objects.get(batch_id=result.batch_id)
    assert row.status == RowStatus.ERROR
    assert row.match_confidence == MatchConfidence.UNMATCHED
    assert "rarity" in row.error_message.lower()


@pytest.mark.django_db
def test_audit_row_save_failure_rolls_back_the_materialized_holding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The collection writes and the ImportRow that audits them commit in ONE transaction,
    so a failed audit-row save rolls the portfolio/item/lot back too -- a committed holding
    can never be orphaned from its audit row (and then silently masked as a duplicate on
    re-import). The row failure is contained: a fresh ERROR row is recorded outside the
    rolled-back block and the batch continues (the run_valuation snapshot/run atomicity
    pattern, applied per-row)."""
    _printing()
    _record_reconciliation()

    real_save = ImportRow.save
    state = {"failed_once": False}

    def _flaky_save(self: ImportRow, *args: Any, **kwargs: Any) -> None:
        # Fail the first save (the in-transaction audit write, after _materialize committed
        # its lot), then let the post-rollback ERROR-row save through.
        if not state["failed_once"]:
            state["failed_once"] = True
            raise RuntimeError("audit insert boom")
        real_save(self, *args, **kwargs)

    monkeypatch.setattr(ImportRow, "save", _flaky_save)

    result = run_import(_csv(_row()), filename="ds.csv")

    # The lot/item/portfolio rolled back with the failed audit row -- no orphaned holding.
    assert CollectionLot.objects.count() == 0
    assert CollectionItem.objects.count() == 0
    assert Portfolio.objects.count() == 0
    # ...and the failure is still recorded as an ERROR row (not lost).
    assert result.rows_error == 1
    assert result.rows_materialized == 0
    row = ImportRow.objects.get(batch_id=result.batch_id)
    assert row.status == RowStatus.ERROR
    assert "audit insert boom" in row.error_message


@pytest.mark.django_db
def test_unexpected_loop_failure_records_failed_batch_not_stuck_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected failure mid-loop (here match_row raises on the 2nd row, after the 1st
    already materialized) must not leave the batch stuck in PROCESSING: run_import records a
    terminal FAILED status + error and re-raises, so a re-import doesn't silently SKIP the
    committed work against a never-resolved batch.
    Per-row independence holds -- the 1st row's already-committed holding is not rolled back."""
    _printing()
    _record_reconciliation()

    calls = {"n": 0}

    def _flaky_match(data: dict[str, Any]) -> MatchResult:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("matcher boom")
        return real_match_row(data)

    monkeypatch.setattr("apps.imports.sync.match_row", _flaky_match)

    content = _csv(_row(), _row(card_name="Second Card", card_number="ZZZ-EN002"))
    with pytest.raises(RuntimeError, match="matcher boom"):
        run_import(content, filename="ds.csv")

    batch = ImportBatch.objects.get()
    assert batch.status == ImportStatus.FAILED
    assert "matcher boom" in batch.error
    # The first row materialized before the failure and stays committed (per-row independence).
    assert CollectionLot.objects.count() == 1


@pytest.mark.django_db
def test_blank_cost_and_date_materialize_with_nulls() -> None:
    """A blank Price Bought / Date Bought is the normal "unknown" state (not an issue),
    so the row materializes with a NULL cost / date lot."""
    _printing()
    _record_reconciliation()

    run_import(_csv(_row(price="", acquired="")), filename="ds.csv")

    lot = CollectionLot.objects.get()
    assert lot.unit_cost is None
    assert lot.acquired_at is None


# --- re-import dedup (per-holding) ----------------------------------------------


@pytest.mark.django_db
def test_reimport_of_unchanged_snapshot_skips_the_duplicate_lot() -> None:
    _printing()
    _record_reconciliation()
    run_import(_csv(_row()), filename="ds.csv")

    result = run_import(_csv(_row()), filename="ds.csv")  # same snapshot again

    assert result.rows_skipped_duplicate == 1
    assert result.rows_materialized == 0
    assert CollectionLot.objects.count() == 1  # no duplicate lot
    row = ImportRow.objects.get(batch_id=result.batch_id)
    assert row.status == RowStatus.SKIPPED


@pytest.mark.django_db
def test_reimport_with_changed_quantity_routes_to_review_without_touching_the_lot() -> None:
    _printing()
    _record_reconciliation()
    run_import(_csv(_row(quantity="3")), filename="ds.csv")

    result = run_import(_csv(_row(quantity="5")), filename="ds.csv")

    assert result.rows_pending_review == 1
    assert result.rows_materialized == 0
    assert CollectionLot.objects.count() == 1  # original kept, not duplicated or overwritten
    assert CollectionLot.objects.get().quantity == 3
    row = ImportRow.objects.get(batch_id=result.batch_id)
    assert row.status == RowStatus.PENDING
    assert "changed" in row.error_message.lower()


# --- batch-level + counts -------------------------------------------------------


@pytest.mark.django_db
def test_non_dragon_shield_file_records_a_failed_batch() -> None:
    result = run_import("col_a,col_b\n1,2\n", filename="bad.csv")

    assert result.status == ImportStatus.FAILED.value
    assert result.rows_total == 0
    batch = ImportBatch.objects.get(pk=result.batch_id)
    assert batch.status == ImportStatus.FAILED
    assert batch.error
    assert ImportRow.objects.count() == 0


@pytest.mark.django_db
def test_folder_name_is_trimmed_before_portfolio_get_or_create() -> None:
    """Natural-key text fields need trimming so lookups aren't split by incidental
    whitespace: the importer trims Folder Name, so a padded folder resolves to the
    same portfolio."""
    _printing()
    _record_reconciliation()

    run_import(_csv(_row(folder="  Yubel Deck  ")), filename="ds.csv")

    assert Portfolio.objects.get().name == "Yubel Deck"


@pytest.mark.django_db
def test_result_tallies_mixed_outcomes() -> None:
    _printing()  # matches the default ash row -> EXACT
    _record_reconciliation()
    content = _csv(
        _row(),  # EXACT -> materialized
        _row(card_name="Unknown Card", card_number="ZZZ-EN001"),  # no such printing -> UNMATCHED
        _row(rarity="QQ"),  # unmapped rarity -> ERROR
    )

    result = run_import(content, filename="ds.csv")

    assert result.rows_total == 3
    assert result.rows_materialized == 1
    assert result.rows_pending_review == 1
    assert result.rows_error == 1
    assert result.status == ImportStatus.REVIEW.value


@pytest.mark.django_db
def test_empty_file_completes_with_no_rows() -> None:
    _record_reconciliation()

    result = run_import(_csv(), filename="empty.csv")

    assert result.rows_total == 0
    assert result.status == ImportStatus.COMPLETED.value


# --- management command ---------------------------------------------------------


@pytest.mark.django_db
def test_command_imports_and_materializes(tmp_path: Path) -> None:
    _printing()
    _record_reconciliation()
    path = tmp_path / "ds.csv"
    path.write_text(_csv(_row()), encoding="utf-8")
    out = StringIO()

    call_command("import_dragon_shield", str(path), stdout=out)

    assert CollectionLot.objects.count() == 1
    assert "materialized" in out.getvalue()


@pytest.mark.django_db
def test_command_dry_run_writes_nothing(tmp_path: Path) -> None:
    _printing()
    _record_reconciliation()
    path = tmp_path / "ds.csv"
    path.write_text(_csv(_row()), encoding="utf-8")
    out = StringIO()

    call_command("import_dragon_shield", str(path), "--dry-run", stdout=out)

    assert ImportBatch.objects.count() == 0
    assert CollectionLot.objects.count() == 0
    assert "DRY RUN" in out.getvalue()
    assert "1 EXACT" in out.getvalue()


@pytest.mark.django_db
def test_command_warns_when_reconciliation_is_stale(tmp_path: Path) -> None:
    _printing()  # no reconciliation recorded
    path = tmp_path / "ds.csv"
    path.write_text(_csv(_row()), encoding="utf-8")
    out = StringIO()

    call_command("import_dragon_shield", str(path), stdout=out)

    assert CollectionLot.objects.count() == 0
    assert "staged" in out.getvalue().lower()


@pytest.mark.django_db
def test_command_rejects_non_dragon_shield_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("col_a,col_b\n1,2\n", encoding="utf-8")

    with pytest.raises(CommandError):
        call_command("import_dragon_shield", str(path), stdout=StringIO())
