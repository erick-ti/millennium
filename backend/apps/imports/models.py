from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class SourceFormat(models.TextChoices):
    """The source application / CSV layout an import came from.

    Only Dragon Shield is implemented in Phase 3 (the best YGO scanner — BRAINDUMP
    competitive landscape). The column anchors the multi-format intent (TCGplayer and
    a manual-mapped CSV are documented future formats), but like the single-value
    ``Provider`` / ``MetadataSource`` enums it carries only the value a parser emits
    today: adding a format is a ``TextChoices`` edit *plus* a migration to widen the
    CHECK below (the closed-enum gotcha), so the vocabulary stays honest to what runs.
    """

    DRAGON_SHIELD = "dragon_shield", "Dragon Shield"


class ImportStatus(models.TextChoices):
    """Batch-level lifecycle, advanced by the import orchestration (slice 4): PENDING
    on upload, PROCESSING while rows are parsed / normalized / matched, REVIEW once
    rows await human triage, COMPLETED when every row is materialized or skipped,
    FAILED on a batch-level error (e.g. a file that isn't a recognized DS export).
    """

    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    REVIEW = "review", "Review"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class ImportBatch(TimeStampedModel):
    """One uploaded CSV import — the parent of its parsed ``ImportRow`` rows.

    A *mutable* work record (not append-only like ``SyncRun``): ``status`` advances as
    the pipeline processes the batch, so ``updated_at`` is meaningful. Per-status row
    counts are deliberately NOT denormalized here — a batch summary ("12 matched, 3
    need review") is derived by aggregating child rows (the project's "annotate via
    SQL; denormalize only after profiling" discipline, BRAINDUMP quantity rule), so it
    can't drift from the rows it counts.
    """

    source_format = models.CharField(max_length=32, choices=SourceFormat.choices)
    status = models.CharField(
        max_length=16, choices=ImportStatus.choices, default=ImportStatus.PENDING
    )
    original_filename = models.CharField(max_length=255)
    # Batch-level failure reason (blank unless status=FAILED) — e.g. an unrecognized
    # header row. Per-row parse/match failures live on ``ImportRow.error_message``.
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # Closed-vocabulary guards: `choices` is form-layer validation only, so
            # guard each enum at the DB on every backend (the SyncRun / PriceSnapshot
            # precedent) — `.create()` / bulk paths would otherwise persist anything.
            models.CheckConstraint(
                condition=models.Q(source_format__in=SourceFormat.values),
                name="import_batch_source_format_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=ImportStatus.values),
                name="import_batch_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.get_source_format_display()} {self.original_filename} "
            f"({self.get_status_display()})"
        )


class MatchConfidence(models.TextChoices):
    """How sure the matching engine (slice 3) is of a row's resolved printing — a
    *tier*, not a numeric score, so the review queue filters by band and the
    materialization-approval policy (slice 4) reads the tier to decide which rows
    auto-commit vs await review. ``UNMATCHED`` means the matcher found no printing.
    """

    EXACT = "exact", "Exact"
    HIGH = "high", "High"
    MEDIUM = "medium", "Medium"
    LOW = "low", "Low"
    UNMATCHED = "unmatched", "Unmatched"


class RowStatus(models.TextChoices):
    """A row's position in the pipeline — orthogonal to match *quality* (which lives
    on ``match_confidence``). ``PENDING`` spans a freshly parsed row through to one
    awaiting materialization or review; ``MATERIALIZED`` once it becomes a
    collection_item + lot; ``SKIPPED`` when deduplicated on re-import or human-
    rejected; ``ERROR`` when parsing / normalization failed (``error_message`` says
    why). The "needs review" set is *derived* (PENDING with a sub-HIGH confidence),
    not a distinct status, so it can't drift from the confidence tier.
    """

    PENDING = "pending", "Pending"
    MATERIALIZED = "materialized", "Materialized"
    SKIPPED = "skipped", "Skipped"
    ERROR = "error", "Error"


class ImportRow(TimeStampedModel):
    """One data row from an imported CSV — staging for the parse -> normalize -> match
    -> materialize pipeline.

    ``raw_data`` preserves the original row verbatim (header -> value) so it can be
    re-normalized / re-matched when the logic improves (BRAINDUMP "raw data
    preservation"). ``normalized_data`` holds the mapped fields (stripped set_code,
    canonical rarity, edition / condition / language slugs, quantity, unit_cost, date,
    folder) as JSON rather than typed columns: a row is pre-validation staging that
    must be able to *hold* un-mappable values for a human to fix, and typed CHECK
    columns would reject exactly the dirty input the review queue exists to triage.
    The resolved match, by contrast, IS typed — ``matched_printing`` + a confidence
    tier + status — the single best match the engine picks; a human overrides it via
    the review API (slice 5).

    ``matched_printing`` is ``SET_NULL``: a re-derivable soft pointer (re-run the
    matcher), so deleting a printing isn't blocked by stale staging rows. SET_NULL
    nulls the FK but does NOT reset ``match_confidence`` (the FK collector bypasses
    ``save()``), so a deleted-printing row can read ``matched_printing=NULL`` beside a
    now-stale tier. ``matched_printing`` is therefore the *authoritative* match signal —
    confidence is only a quality tier *of* a present match, meaningless without one —
    so the matcher / materializer (slices 3-4) treat a NULL printing as unmatched
    regardless of the stale tier, and a re-match overwrites both. (PROTECT is wrong
    here: staging must not block printing cleanup. A DB CHECK tying the two is wrong
    too: on a printing delete it would make the DELETE fail rather than null the
    pointer — worse than a stale tier consumers already ignore.) This mirrors
    ``CollectionItem.storage_location`` (an optional annotation) and contrasts with the
    ``PROTECT`` FKs that guard real, non-re-derivable downstream data. The ``batch`` FK
    is ``CASCADE``: rows are composition of their batch.
    """

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="rows")
    # 1-based position in the source file, for display, stable ordering, and error
    # reporting. Unique within a batch (one row per source line).
    row_number = models.PositiveIntegerField()
    raw_data = models.JSONField()
    # NULL until the normalization step runs (slice 2) — distinct from {} ("normalized
    # to nothing", which would be meaningless).
    normalized_data = models.JSONField(null=True, blank=True)
    # The single best printing the matcher resolved (slice 3); NULL while unmatched.
    matched_printing = models.ForeignKey(
        "cards.CardPrinting",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_rows",
    )
    match_confidence = models.CharField(
        max_length=16, choices=MatchConfidence.choices, default=MatchConfidence.UNMATCHED
    )
    status = models.CharField(max_length=16, choices=RowStatus.choices, default=RowStatus.PENDING)
    # Per-row parse / normalize / match failure detail for the review UI (blank otherwise).
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["batch", "row_number"]
        constraints = [
            # One row per source line within a batch. Both columns non-null -> a plain
            # UNIQUE, created and exercised on sqlite too (the CollectionItem /
            # ExternalPriceId pattern, not the Postgres-only CardPrinting key).
            models.UniqueConstraint(
                fields=["batch", "row_number"],
                name="unique_import_row_per_batch",
            ),
            # Closed-vocabulary guards (the SyncRun / PriceSnapshot precedent).
            models.CheckConstraint(
                condition=models.Q(match_confidence__in=MatchConfidence.values),
                name="import_row_match_confidence_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=RowStatus.values),
                name="import_row_status_valid",
            ),
        ]
        indexes = [
            # The review queue's hot read: a batch's rows filtered by status
            # ("show this import's rows that still need review / were materialized").
            models.Index(fields=["batch", "status"], name="import_row_batch_status_idx"),
        ]

    def __str__(self) -> str:
        return f"Row {self.row_number} of batch {self.batch_id} ({self.get_status_display()})"
