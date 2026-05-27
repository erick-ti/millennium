from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone

from apps.cards.models import CardPrinting
from apps.collection.models import CollectionItem, CollectionLot
from apps.core.models import SyncKind, SyncRun, SyncStatus
from apps.imports.dragon_shield import (
    ImportParseError,
    ParsedRow,
    normalize_row,
    parse_dragon_shield,
)
from apps.imports.matching import match_row
from apps.imports.models import (
    ImportBatch,
    ImportRow,
    ImportStatus,
    MatchConfidence,
    RowStatus,
    SourceFormat,
)
from apps.portfolio.models import Portfolio

logger = structlog.get_logger(__name__)

# import_source_ref scheme (DECISIONS 2026-05-26 slice 4). The dedup unit the user chose
# is the *holding*: a Dragon Shield export is a full-collection snapshot with no per-row
# ids, so re-importing it should find-or-create the same lot, not duplicate it. A
# CollectionItem's id IS the holding identity (its natural key resolves to one row), so
# one ref per holding. The "dragon_shield" prefix scopes dedup to this source — a future
# format's lot for the same holding carries a different prefix and won't collide — and the
# CollectionLot UniqueConstraint(collection_item, import_source_ref) makes the find-or-create
# race-safe at the DB.
_IMPORT_SOURCE_PREFIX = "dragon_shield"


def _import_source_ref(item: CollectionItem) -> str:
    return f"{_IMPORT_SOURCE_PREFIX}:item:{item.pk}"


class ImportRowNotActionable(Exception):
    """A review action was attempted on a row not in a state that permits it — e.g.
    approving a row with no matched printing, or acting on an already-resolved (non-PENDING)
    row. The review API (slice 5) maps this to HTTP 400; the message is reviewer-facing."""


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Per-run counts plus the batch outcome from one ``run_import`` pass (the
    ``ValuationResult`` shape). All fields are JSON-native so the result serializes for a
    caller / log / future Celery backend."""

    batch_id: int
    status: str
    rows_total: int = 0
    rows_materialized: int = 0
    rows_skipped_duplicate: int = 0
    rows_pending_review: int = 0
    rows_error: int = 0
    materialization_allowed: bool = False


@dataclass(frozen=True, slots=True)
class ImportPreview:
    """Read-only ``--dry-run`` tally: what an import *would* do, written nowhere."""

    rows_total: int = 0
    rows_with_issues: int = 0
    exact: int = 0
    medium: int = 0
    unmatched: int = 0
    reconciliation_fresh: bool = False


def _latest_reconciliation_cutoff() -> datetime | None:
    """The ``created_at`` of the latest successful TCGCSV reconciliation today, or ``None``
    if none ran today — the slice-4 materialization gate's reference time (DECISIONS
    2026-05-26 slice 3 round-4, evolved across two Codex reviews to a per-printing check).

    The DS matcher reads ``CardPrinting.is_multi_variant``, of which TCGCSV reconciliation is
    the sole (set-only) writer. A printing the reconciliation never saw reads
    ``is_multi_variant=False`` (the default) and so matches EXACT even if it is really an
    ambiguous multi-variant placeholder — the fail-open the *pure* matcher cannot close. An
    EXACT match is trustworthy only if the matched printing was **covered** by a recent
    reconciliation, which the caller checks **per printing**: materialize an EXACT row only
    when this cutoff exists (a same-day reconciliation ran) AND ``printing.created_at < cutoff``
    (the printing existed when that reconciliation ran, so its flag reflects current TCGCSV).

    Why a per-printing ``created_at`` check against this cutoff, not a batch-level boolean:

    - **Same-day** bounds provider-side staleness (the ``run_valuation`` precedent): TCGCSV can
      add a second sellable variant to an existing key with no catalog change, so the
      reconciliation must be recent, not merely ever-run. (``created_at__date`` truncates in the
      UTC session TZ Django pins.) This still leaves a <1-day residual — a TCGCSV change *after*
      today's reconciliation isn't caught until the next one — inherent to daily reconciliation.
    - **Per-printing ``created_at``** (not a once-per-batch flag, nor a metadata-``SyncRun``
      ordering) closes two holes a global gate leaves (Codex review, round 2 → 3): a batch
      boolean is a stale snapshot applied to every row, and metadata writes ``CardPrinting``
      rows *before* recording its ``SyncRun`` — so a printing created mid-import by a concurrent
      or partially-failed metadata sync slips a ``SyncRun``-ordering gate (its ``SyncRun`` isn't
      recorded yet) yet is genuinely uncovered. ``created_at`` is set at catalog entry and is
      *stable* — reconciliation corrects rarity with an in-place UPDATE that bumps ``updated_at``,
      not ``created_at`` (verified) — so it is the right coverage proxy, robust where an
      ``updated_at`` comparison would not be.

    Inherent residual (accepted, not closed by this gate): a metadata sync running *concurrently
    with* a reconciliation can create a printing after that run's catalog read but before its
    completion — ``created_at < cutoff`` yet uncovered — because ``SyncRun`` records only the
    completion time, not the catalog-read start. Narrow (requires concurrent metadata+pricing,
    which hold different advisory locks) and bounded (the multi-variant placeholder is
    valuation-tolerant and correctable). Full closure needs a per-printing reconciliation-
    *coverage* signal (reconciliation stamping each printing it processes), deferred to slice 5.

    When an EXACT row is not covered, it is *staged* PENDING rather than materialized, so a
    later import (after ``sync_tcgcsv`` re-runs) or the slice-5 review API commits it — the
    import is never lost, only the auto-commit is held. The coverage decision stays an
    orchestration concern, never coupled into the pure matcher (the valuation precedent).
    """
    return (
        SyncRun.objects.filter(
            kind=SyncKind.TCGCSV_PRICING,
            status=SyncStatus.SUCCESS,
            created_at__date=timezone.localdate(),
        )
        .order_by("-created_at", "-id")
        .values_list("created_at", flat=True)
        .first()
    )


def run_import(content: str, *, filename: str) -> ImportResult:
    """Import one Dragon Shield CSV: parse → stage rows → normalize → match → materialize
    EXACT matches, recording an ``ImportBatch`` + its ``ImportRow``s. The single entry
    point the management command (and the slice-5 API) call — the ``run_*_sync`` precedent.

    The batch is created first (PROCESSING), so even a parse failure leaves a FAILED
    ``ImportBatch`` record for the import history. A non-DS file raises ``ImportParseError``
    inside ``parse_dragon_shield`` → the batch is marked FAILED and the function returns
    (no rows persisted). Otherwise each source row is processed independently (one dirty
    row never aborts the batch — the slice-2 per-row posture): its verbatim ``raw_data`` and
    mapped ``normalized_data`` are stored, the matcher resolves a printing + confidence
    tier, and the row is routed by this table (DECISIONS 2026-05-26 slice 4):

        normalization issue(s)             -> ERROR  (cannot materialize cleanly; the
                                                       human fixes the source / edits)
        clean, MEDIUM or UNMATCHED          -> PENDING (human review, slice 5)
        clean, EXACT, printing uncovered    -> PENDING (staged — no reconciliation today, or
                                                        the printing post-dates the latest one)
        clean, EXACT, covered, new holding  -> MATERIALIZED (folder->portfolio, item, lot)
        clean, EXACT, covered, dup unchanged -> SKIPPED (per-holding re-import dedup)
        clean, EXACT, covered, dup changed  -> PENDING  (qty/cost differs -> review)

    Only EXACT auto-materializes (DECISIONS 2026-05-26 slice 3), and only when the matched
    printing was covered by a same-day TCGCSV reconciliation — a per-printing check
    (``_latest_reconciliation_cutoff`` + ``printing.created_at < cutoff``), not a batch-wide
    flag. The batch ends REVIEW if any row needs human attention (PENDING or ERROR), else
    COMPLETED.

    There is deliberately no batch-wide transaction: per-row independence means a single
    failed materialization marks that one row ERROR and the rest proceed (each row's
    portfolio/item/lot writes are atomic on their own). But an unexpected failure in the
    loop or finalization *itself* (a DB error in ``match_row`` / a non-materialize save, or a
    bug) records the batch FAILED and re-raises, so it never lingers in PROCESSING with
    committed rows unaccounted for — distinct from a parse failure, which returns a FAILED
    result so the caller reports "not a DS file" (Codex review 2026-05-26). And no advisory
    lock: the
    get-then-create paths are race-safe via ``get_or_create`` + the
    ``(collection_item, import_source_ref)`` UNIQUE, unlike the syncs' beat-vs-manual races
    (imports are user-triggered, not scheduled). ``content`` is already-decoded text — the
    command decodes the upload as utf-8-sig (BOM-tolerant); the parser strips a BOM too.
    """
    batch = ImportBatch.objects.create(
        source_format=SourceFormat.DRAGON_SHIELD,
        original_filename=filename,
        status=ImportStatus.PROCESSING,
    )
    try:
        parsed_rows = parse_dragon_shield(content)
    except ImportParseError as exc:
        batch.status = ImportStatus.FAILED
        batch.error = str(exc)
        batch.save(update_fields=["status", "error", "updated_at"])
        logger.warning("import.parse_failed", batch_id=batch.pk, error=str(exc))
        return ImportResult(batch_id=batch.pk, status=ImportStatus.FAILED.value)

    # The cutoff is the latest same-day reconciliation's time; an EXACT row materializes only
    # if its matched printing predates it (checked per-row in _process_row, not as a batch-wide
    # snapshot). None = no reconciliation today -> every EXACT row stages. materialization_allowed
    # records the batch-level "could anything auto-materialize today" for the result / command
    # summary; per-printing coverage staging surfaces per-row (rows_pending_review), not here.
    reconciliation_cutoff = _latest_reconciliation_cutoff()
    materialization_allowed = reconciliation_cutoff is not None
    if not materialization_allowed:
        logger.warning("import.reconciliation_stale_staging_exact_rows", batch_id=batch.pk)

    tally = {
        RowStatus.MATERIALIZED: 0,
        RowStatus.SKIPPED: 0,
        RowStatus.PENDING: 0,
        RowStatus.ERROR: 0,
    }
    try:
        for parsed in parsed_rows:
            outcome = _process_row(batch, parsed, reconciliation_cutoff=reconciliation_cutoff)
            tally[outcome] += 1

        final_status = (
            ImportStatus.REVIEW
            if tally[RowStatus.PENDING] or tally[RowStatus.ERROR]
            else ImportStatus.COMPLETED
        )
        batch.status = final_status
        batch.save(update_fields=["status", "updated_at"])
        logger.info(
            "import.completed",
            batch_id=batch.pk,
            status=final_status.value,
            materialized=tally[RowStatus.MATERIALIZED],
            skipped=tally[RowStatus.SKIPPED],
            pending=tally[RowStatus.PENDING],
            error=tally[RowStatus.ERROR],
        )
        return ImportResult(
            batch_id=batch.pk,
            status=final_status.value,
            rows_total=len(parsed_rows),
            rows_materialized=tally[RowStatus.MATERIALIZED],
            rows_skipped_duplicate=tally[RowStatus.SKIPPED],
            rows_pending_review=tally[RowStatus.PENDING],
            rows_error=tally[RowStatus.ERROR],
            materialization_allowed=materialization_allowed,
        )
    except Exception as exc:
        # An unexpected failure in the row loop or at finalization (a DB error in match_row /
        # a non-materialize row.save / the batch.save, or a bug) must not leave the batch
        # stuck PROCESSING with earlier rows already committed -- a re-import would then SKIP
        # those lots via the per-holding dedup while this batch never reaches a terminal
        # status, hiding the partial failure from the review/history flow (Codex review
        # 2026-05-26). Record FAILED + the error and re-raise (the real cause, not the
        # "not a DS file" parse-failure path). Per-row materialize/audit failures are already
        # contained as ERROR rows inside _process_row, so this guards the row-independent loop
        # itself, never a single row -- already-committed rows stay (per-row independence
        # holds). Best-effort: if the cause is a dead connection this FAILED write can't land
        # either, but it converts every reachable-DB failure from a silent stuck batch into an
        # audited one.
        logger.error("import.batch_failed", batch_id=batch.pk, error=str(exc))
        batch.status = ImportStatus.FAILED
        batch.error = str(exc)
        batch.save(update_fields=["status", "error", "updated_at"])
        raise


def _process_row(
    batch: ImportBatch, parsed: ParsedRow, *, reconciliation_cutoff: datetime | None
) -> RowStatus:
    """Stage, match, and (if eligible) materialize one source row; return its final
    ``RowStatus``. Saves exactly one ``ImportRow`` and, for an auto-materialized row, the
    portfolio/item/lot it created. ``reconciliation_cutoff`` is the latest same-day
    reconciliation's time (None if none today); an EXACT row materializes only if its matched
    printing predates it — the per-printing coverage check (see ``_latest_reconciliation_cutoff``)."""
    normalized = normalize_row(parsed.raw)
    row = ImportRow(
        batch=batch,
        row_number=parsed.row_number,
        raw_data=parsed.raw,
        normalized_data=normalized.data,
    )

    if normalized.issues:
        # Any normalization issue -> ERROR. The row can't build a clean natural key / lot,
        # and silently nulling a present-but-unparseable cost/date would lose data (slice 2
        # flags, never guesses). No match is attempted; the human fixes it (source / slice 5).
        row.match_confidence = MatchConfidence.UNMATCHED
        row.status = RowStatus.ERROR
        row.error_message = "; ".join(normalized.issues)
        row.save()
        return RowStatus.ERROR

    match = match_row(normalized.data)
    row.matched_printing = match.printing
    row.match_confidence = match.confidence

    if match.confidence != MatchConfidence.EXACT or match.printing is None:
        # MEDIUM (printing found but not safe to auto-commit) / UNMATCHED -> human review
        # (slice 5). matched_printing keeps the best candidate for MEDIUM; detail explains.
        row.status = RowStatus.PENDING
        row.error_message = match.detail
        row.save()
        return RowStatus.PENDING

    # Coverage gate: auto-materialize only if a same-day reconciliation ran (cutoff exists)
    # AND this matched printing predates it -- so the reconciliation saw the printing and its
    # is_multi_variant flag is trustworthy (DECISIONS 2026-05-26 slice 4). The per-printing
    # created_at check (not a once-per-batch flag) closes the concurrent / partially-failed
    # metadata-sync hole a global gate leaves: a printing created mid-import slips a SyncRun-
    # ordering gate but not its own created_at (Codex review, round 3). Otherwise stage PENDING.
    if reconciliation_cutoff is None:
        row.status = RowStatus.PENDING
        row.error_message = (
            "EXACT match staged for review: no successful TCGCSV reconciliation recorded "
            "today, so the multi-variant guard may be stale. Run sync_tcgcsv, then "
            "re-import or approve."
        )
        row.save()
        return RowStatus.PENDING
    if match.printing.created_at >= reconciliation_cutoff:
        row.status = RowStatus.PENDING
        row.error_message = (
            "EXACT match staged for review: the matched printing was created after today's "
            "TCGCSV reconciliation, so it is not yet covered (its multi-variant status is "
            "unchecked). Re-run sync_tcgcsv, then re-import or approve."
        )
        row.save()
        return RowStatus.PENDING

    try:
        with transaction.atomic():
            outcome, message = _materialize(normalized.data, match.printing)
            # The ImportRow recording the materialization commits in the SAME transaction as
            # the portfolio/item/lot it audits (the run_valuation snapshot/run atomicity
            # precedent, DECISIONS 2026-05-25 slice 4c): a failed audit save rolls the
            # collection writes back too, so a committed holding can never be orphaned from --
            # and then silently masked as a duplicate on re-import by -- a missing audit row.
            row.status = outcome
            row.error_message = message
            row.save()
    except Exception as exc:
        # Collection writes and the audit row rolled back together. Record a fresh ERROR row
        # OUTSIDE the rolled-back block (the run_valuation FAILED-after-rollback pattern);
        # reset row.pk first so this is a clean INSERT, not an UPDATE of the row the rollback
        # discarded (which would silently no-op). The trigger is rare -- a DB CHECK rejecting
        # a value the normalizer should have caught, or an infra error in the commit window --
        # so this marks just this row ERROR and the batch continues.
        logger.error(
            "import.row_materialize_failed",
            batch_id=batch.pk,
            row_number=parsed.row_number,
            error=str(exc),
        )
        row.pk = None
        row.status = RowStatus.ERROR
        row.error_message = f"materialization failed: {exc}"
        row.save()
        return RowStatus.ERROR

    return outcome


def _materialize(data: dict[str, Any], printing: CardPrinting) -> tuple[RowStatus, str]:
    """Commit one EXACT row into the collection: find-or-create the folder's portfolio and
    the holding, then find-or-create its single import lot. Returns the row's outcome +
    a note. Called only for a clean EXACT row under a fresh reconciliation, so every
    identity field is present and valid (the normalizer flags absent/unmapped ones, which
    route to ERROR before here) and ``printing`` is non-NULL.

    Per-holding dedup (the user's choice): the lot's ``import_source_ref`` is derived from
    the holding, so re-importing the same snapshot find-or-creates the *same* lot. An
    unchanged re-import SKIPs; one whose quantity/cost/date differs routes to review (a
    real change vs an accidental re-import is a human call), never silently duplicating or
    overwriting.
    """
    portfolio, _ = Portfolio.objects.get_or_create(name=data["portfolio_name"])
    item, _ = CollectionItem.objects.get_or_create(
        printing=printing,
        portfolio=portfolio,
        condition=data["condition"],
        edition=data["edition"],
        language=data["language"],
    )
    ref = _import_source_ref(item)
    quantity = data["quantity"]
    raw_cost = data["unit_cost"]
    raw_date = data["acquired_at"]
    unit_cost = Decimal(str(raw_cost)) if raw_cost is not None else None
    acquired_at = date.fromisoformat(str(raw_date)) if raw_date is not None else None

    lot, created = CollectionLot.objects.get_or_create(
        collection_item=item,
        import_source_ref=ref,
        defaults={"quantity": quantity, "unit_cost": unit_cost, "acquired_at": acquired_at},
    )
    if created:
        return RowStatus.MATERIALIZED, ""
    if (lot.quantity, lot.unit_cost, lot.acquired_at) == (quantity, unit_cost, acquired_at):
        return RowStatus.SKIPPED, f"duplicate of an existing imported lot ({ref}); skipped"
    return (
        RowStatus.PENDING,
        f"holding already imported ({ref}) but quantity/cost/date changed "
        f"(was {lot.quantity}x @ {lot.unit_cost} on {lot.acquired_at}; "
        f"now {quantity}x @ {unit_cost} on {acquired_at}) -- review",
    )


def preview_import(content: str) -> ImportPreview:
    """Read-only dry run for ``import_dragon_shield --dry-run``: parse + normalize + match,
    tallying the match tiers and the reconciliation-freshness gate, writing nothing — no
    batch, rows, or holdings. Raises ``ImportParseError`` on a non-DS file (the command
    reports it). Dedup is deliberately NOT simulated (it would need to read would-be
    holdings), so a row counted EXACT here may still SKIP as a duplicate on a real import.
    """
    parsed_rows = parse_dragon_shield(content)
    issues = exact = medium = unmatched = 0
    for parsed in parsed_rows:
        normalized = normalize_row(parsed.raw)
        if normalized.issues:
            issues += 1
            continue
        confidence = match_row(normalized.data).confidence
        if confidence == MatchConfidence.EXACT:
            exact += 1
        elif confidence == MatchConfidence.MEDIUM:
            medium += 1
        else:
            unmatched += 1
    return ImportPreview(
        rows_total=len(parsed_rows),
        rows_with_issues=issues,
        exact=exact,
        medium=medium,
        unmatched=unmatched,
        reconciliation_fresh=_latest_reconciliation_cutoff() is not None,
    )


# --- review actions (slice 5) ---------------------------------------------------
# The review API resolves rows run_import staged PENDING (MEDIUM / UNMATCHED / a gate-held
# EXACT) through three actions, all going through the same _materialize chokepoint the
# automatic path uses (never re-implementing the collection writes — DECISIONS 2026-05-26
# slice 4). They are plain orchestration functions (DRF-free, like run_import): the viewset
# applies HTTP, these own the state transition + the collection writes.
#
# Each action, inside one transaction, (1) locks the parent batch and requires it to be in
# REVIEW, then (2) reloads its row under a row lock and re-checks status on the *fresh* instance
# (not the one the request fetched). The row re-check stops two concurrent POSTs on the same row
# from both passing the PENDING check and diverging — a stale approve clobbering a concurrent
# override, an approve+reject leaving a committed lot marked SKIPPED, or sibling approvals
# recomputing the batch from an uncommitted view (Codex review round 1). The batch-REVIEW gate
# stops a row being actioned while its batch is still PROCESSING (run_import owns it and would
# finalize from its own tally) or FAILED (a partial run's leftover committed rows — re-import,
# don't cherry-pick); without it the action mutates the row but _recompute_batch_status no-ops
# on a non-REVIEW batch, leaving the two inconsistent (Codex review round 3). Single-user, but a
# FAILED batch's leftover PENDING rows are a reachable steady state and double-clicks / two tabs
# reach the races, and the project closes audit/collection-divergence (the slice-4 precedent). On
# sqlite the locks no-op (writes serialize already, like the advisory locks); the re-checks they
# guard run on every backend. Consistent batch→row lock order across all three → no deadlock.


def _lock_review_batch(batch_id: int) -> ImportBatch:
    """Lock the parent batch and require it to be in REVIEW — the only phase with rows awaiting
    human action. Refuses a row whose batch is PROCESSING (``run_import`` owns it and finalizes
    from its own tally — a review action must not race that), FAILED (a broken/partial import;
    re-import rather than cherry-pick its committed leftover rows), PENDING (pre-processing), or
    COMPLETED (no PENDING rows remain). Every legitimately-actionable PENDING row lives in a
    REVIEW batch, so this blocks nothing valid. The lock serializes against ``run_import``'s final
    ``batch.save`` (its plain UPDATE waits on this row lock) and against sibling review actions;
    the returned locked batch is reused by ``_recompute_batch_status`` (DECISIONS 2026-05-27
    round 3)."""
    batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status != ImportStatus.REVIEW:
        raise ImportRowNotActionable(
            f"batch {batch.pk} is {batch.get_status_display()}, not in Review; its rows can't be "
            "actioned — a still-processing or failed import is not reviewable (re-import instead)"
        )
    return batch


def _lock_row(row_id: int) -> ImportRow:
    """Reload an ``ImportRow`` under a row-level lock for the enclosing ``transaction.atomic``.
    ``of=("self",)`` locks only the import_row table: a plain ``select_for_update()`` alongside
    the nullable ``matched_printing`` select_related would try to lock the nullable side of an
    outer join — a Postgres error. The joined rows are read, not locked (we never mutate them)."""
    return (
        ImportRow.objects.select_for_update(of=("self",))
        .select_related("batch", "matched_printing", "matched_printing__card")
        .get(pk=row_id)
    )


def _require_pending(row: ImportRow, verb: str) -> None:
    if row.status != RowStatus.PENDING:
        raise ImportRowNotActionable(
            f"row {row.pk} is {row.get_status_display()}, not Pending; "
            f"only a pending row can be {verb}"
        )


def approve_row(row: ImportRow) -> tuple[ImportRow, RowStatus]:
    """Materialize a human-approved PENDING row through the ``_materialize`` chokepoint; return
    the fresh (locked) row + its outcome — the review API's "approve" action.

    A reviewer's explicit approval **overrides the automatic freshness gate** (DECISIONS
    2026-05-27): ``run_import`` stages an EXACT row PENDING unless a same-day TCGCSV
    reconciliation *covers* the matched printing, because the *automatic* path has no human
    to weigh the ``is_multi_variant`` fail-open (a not-yet-reconciled multi-variant placeholder
    reads as a confident match) — but review IS that human attention, so approval commits
    regardless of reconciliation freshness. A known multi-variant placeholder
    (``is_multi_variant=True``, surfaced to the reviewer by the serializer) is likewise
    approvable: the human accepts the generic placeholder (v1 has no per-variant rows to pick).

    Only a PENDING row with a ``matched_printing`` is approvable — an UNMATCHED row must be
    ``override_row``'d to a chosen printing first, and an ERROR row (normalization failed → no
    clean ``normalized_data``) is terminal (fix the source and re-import). The row is reloaded
    under a lock and its status re-checked on that fresh instance, then ``_materialize``
    (portfolio/item/lot find-or-create + per-holding dedup) and the audit-row save commit in one
    ``transaction.atomic`` — the ``_process_row`` snapshot/audit atomicity, so a failed audit
    save rolls the holding back too. Reloading also means the *current* matched printing is
    materialized (a concurrent override can't be clobbered by this caller's stale instance).
    Returns the outcome: MATERIALIZED (new holding) / SKIPPED (unchanged duplicate) / PENDING
    (the holding was already imported with a different quantity/cost/date — a conflict the API
    surfaces rather than silently overwriting historical cost basis; the row is left PENDING)."""
    with transaction.atomic():
        # Lock the batch (require REVIEW) before the row — consistent order across actions → no
        # deadlock; the batch lock also serializes the sibling-row recompute race (round 1).
        batch = _lock_review_batch(row.batch_id)
        locked = _lock_row(row.pk)
        _require_pending(locked, "approved")
        if locked.matched_printing is None:
            raise ImportRowNotActionable(
                f"row {locked.pk} has no matched printing; override it to a printing before approving"
            )
        if not locked.normalized_data:
            raise ImportRowNotActionable(f"row {locked.pk} has no normalized data to materialize")
        outcome, message = _materialize(locked.normalized_data, locked.matched_printing)
        locked.status = outcome
        locked.error_message = message
        locked.save(update_fields=["status", "error_message", "updated_at"])
        _recompute_batch_status(batch)

    logger.info(
        "import.row_approved", row_id=locked.pk, batch_id=locked.batch_id, outcome=outcome.value
    )
    return locked, outcome


def override_row(row: ImportRow, printing: CardPrinting) -> ImportRow:
    """Point a PENDING row at a human-chosen ``CardPrinting`` (re-checked under a row lock);
    return the fresh row — the review API's "override" action. Sets ``matched_printing`` only
    and leaves the row PENDING, so the corrected match (and its ``is_multi_variant`` flag) can be
    eyeballed, then ``approve_row``'d.

    ``match_confidence`` is deliberately left at the matcher's original verdict: per the
    ``ImportRow`` doctrine ``matched_printing`` is the *authoritative* match signal and
    confidence is only a matcher-output quality tier — so a human override changes the
    authoritative pointer without forging a matcher tier it never produced (there is no MANUAL
    tier; inventing one would be a CHECK-altering enum migration). ``approve_row`` keys on
    PENDING + a present ``matched_printing``, not on confidence, so an overridden UNMATCHED row
    is immediately approvable. The parent batch is locked and required to be in REVIEW (like the
    other actions — uniform batch→row order), but it is left as-is: the row stays PENDING, so no
    batch-status recompute is needed."""
    with transaction.atomic():
        _lock_review_batch(row.batch_id)
        locked = _lock_row(row.pk)
        _require_pending(locked, "overridden")
        locked.matched_printing = printing
        locked.error_message = (
            f"matched printing overridden via review to {printing.card.name} "
            f"[{printing.set_code} {printing.set_rarity}]"
        )
        locked.save(update_fields=["matched_printing", "error_message", "updated_at"])
    logger.info("import.row_overridden", row_id=locked.pk, printing_id=printing.pk)
    return locked


def reject_row(row: ImportRow) -> ImportRow:
    """Mark a PENDING row SKIPPED (re-checked under a row lock); return the fresh row — the
    review API's "reject" action: the reviewer declines to import this holding. SKIPPED is the
    same terminal state a deduplicated re-import lands in (the model's documented "deduplicated
    on re-import or human-rejected" semantics). Only PENDING rows are rejectable: a MATERIALIZED
    row already wrote a holding (un-materializing would mean deleting collection data, out of v1
    scope) and an ERROR row is already terminal."""
    with transaction.atomic():
        batch = _lock_review_batch(row.batch_id)
        locked = _lock_row(row.pk)
        _require_pending(locked, "rejected")
        locked.status = RowStatus.SKIPPED
        locked.error_message = "rejected via review"
        locked.save(update_fields=["status", "error_message", "updated_at"])
        _recompute_batch_status(batch)
    logger.info("import.row_rejected", row_id=locked.pk, batch_id=locked.batch_id)
    return locked


def _recompute_batch_status(batch: ImportBatch) -> None:
    """Re-derive a batch's status from its rows after a review action: COMPLETED when no row
    still needs attention (none PENDING or ERROR), else REVIEW — mirroring ``run_import``'s
    finalization (REVIEW if any PENDING/ERROR else COMPLETED), so a batch progresses to
    COMPLETED as the reviewer clears its queue. Only a batch already in the post-processing
    REVIEW/COMPLETED band is touched; a PENDING/PROCESSING/FAILED batch is left alone (a FAILED
    parse has no rows; an in-flight batch is ``run_import``'s to finalize). ERROR rows keep a
    batch in REVIEW — they are terminal (fix the source and re-import), so a batch with
    unresolved ERROR rows never auto-completes, by design.

    The caller (approve/reject) holds a ``select_for_update`` lock on ``batch``, which serializes
    concurrent sibling-row actions through here — without it, two could each read the rows before
    the other commits and both leave the batch REVIEW after its last row resolved."""
    if batch.status not in (ImportStatus.REVIEW, ImportStatus.COMPLETED):
        return
    has_open = batch.rows.filter(status__in=[RowStatus.PENDING, RowStatus.ERROR]).exists()
    new_status = ImportStatus.REVIEW if has_open else ImportStatus.COMPLETED
    if new_status != batch.status:
        batch.status = new_status
        batch.save(update_fields=["status", "updated_at"])
