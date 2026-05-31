from __future__ import annotations

from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base adding self-managed created/updated timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SyncKind(models.TextChoices):
    """Which recurring sync a ``SyncRun`` records. Spans two apps (the YGOPRODeck
    metadata sync in ``cards``; the TCGCSV pricing pipeline in ``pricing``), so it
    lives in ``core`` like the shared ``Edition`` enum rather than on either app."""

    YGOPRODECK_METADATA = "ygoprodeck_metadata", "YGOPRODeck metadata"
    TCGCSV_PRICING = "tcgcsv_pricing", "TCGCSV pricing"


class SyncStatus(models.TextChoices):
    """Outcome of a completed sync run."""

    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"


class SyncRun(TimeStampedModel):
    """Append-only record of one metadata/pricing sync run — the per-sync history
    backing the compare-to-previous cardinality guard (DECISIONS 2026-05-24 slice 3:
    round-4 recurring-safety prerequisite #2).

    Each daily sync writes one row at completion: SUCCESS with the run's fetch
    cardinality, or FAILED with the error (e.g. the truncation-guard message). Before
    a run, the orchestration reads the latest SUCCESS row of its kind and raises the
    provider's fetch floor to ``last_good * (1 - tolerance)``, so an unexpectedly
    shrunken bulk dump is rejected before any write. A rejected run records FAILED,
    never SUCCESS, so a bad fetch can't become the next run's baseline — the floor
    tracks the last-good high-water mark.

    Append-only (the ``PriceSnapshot`` posture): inserted, never updated; the admin
    blocks edit/delete. Chosen over a cache-backed baseline because a cache fails
    *open* on eviction/flush (Redis has no persistence here) — silently disabling the
    guard, the exact failure it exists to catch — and a model doubles as the run
    audit trail the deferred coverage-collapse alerting will build on.
    """

    kind = models.CharField(max_length=32, choices=SyncKind.choices)
    status = models.CharField(max_length=16, choices=SyncStatus.choices)
    # Fetch cardinality, by dimension. Which apply depends on `kind` — metadata fills
    # card/printing counts, pricing fills product/price-row counts — so each is nullable
    # and the other kind's (and a pre-fetch failure's) are left NULL. The guard reads
    # only the dimension relevant to a kind off its latest SUCCESS row. (Archetype
    # coverage is per-run telemetry kept in `detail`, not a guarded count dimension —
    # the Phase 5 archetype guard reads the live tagged set, not a SyncRun baseline.)
    card_count = models.PositiveIntegerField(null=True, blank=True)
    printing_count = models.PositiveIntegerField(null=True, blank=True)
    product_count = models.PositiveIntegerField(null=True, blank=True)
    price_row_count = models.PositiveIntegerField(null=True, blank=True)
    # Full per-run result counts (the asdict of SyncResult / ReconcileResult /
    # IngestResult) for audit. Not read by the guard, so an open shape with no CHECK.
    detail = models.JSONField(default=dict, blank=True)
    # Failure reason when status=FAILED (blank on success).
    error = models.TextField(blank=True, default="")

    class Meta:
        # Most-recent-first within a kind; the guard's lookup is the latest SUCCESS of
        # a kind, served by the index below. `-id` is a stable tiebreaker so "latest"
        # is deterministic even if two runs share a created_at (the CollectionLot
        # ordering lesson, DECISIONS 2026-05-22) — id is monotonic with insertion.
        ordering = ["kind", "-created_at", "-id"]
        constraints = [
            # Closed-vocabulary guards (the PriceSnapshot/CollectionItem precedent):
            # `choices` is form-layer only, so guard each enum at the DB everywhere.
            models.CheckConstraint(
                condition=models.Q(kind__in=SyncKind.values),
                name="sync_run_kind_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=SyncStatus.values),
                name="sync_run_status_valid",
            ),
        ]
        indexes = [
            # The guard's hot lookup: the latest SUCCESS run of a given kind.
            models.Index(fields=["kind", "status", "-created_at"], name="sync_run_latest_idx"),
        ]

    def __str__(self) -> str:
        return (
            f"{self.get_kind_display()} {self.get_status_display()} "
            f"({self.created_at:%Y-%m-%d %H:%M})"
        )
