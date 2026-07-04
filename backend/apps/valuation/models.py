from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class ValuationStatus(models.TextChoices):
    """Outcome of a valuation run. SUCCESS/FAILED mirror ``SyncStatus``; SKIPPED is the
    extra terminal state unique to valuation -- a run refused because its hard
    dependency (a successful same-day TCGCSV pricing run) wasn't met yet."""

    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class ValuationRun(TimeStampedModel):
    """Append-only record of one valuation pass -- the run history backing the
    orchestration that wraps the valuation engine.

    Each scheduled/manual run writes one row at completion:
    - SUCCESS with the pass's counts (the next-day audit / coverage-collapse baseline),
    - FAILED with the error (the engine's ``transaction.atomic`` already rolled back any
      partial snapshots, so a FAILED run left no half-written series), or
    - SKIPPED with the reason when the run was refused because no successful same-day
      TCGCSV pricing ``SyncRun`` exists yet. Valuation reads the price table and that
      table is filled incrementally by pricing's ingest, so valuing before today's
      pricing succeeded could roll a mixed/stale price set into the day's snapshot --
      which is unique-per-day and admin-delete-blocked, hence uncorrectable.

    A dedicated model rather than a ``SyncKind`` on ``core.SyncRun``: valuation does no
    fetch (it reads local data, so there's no fetch-floor guard and none of SyncRun's
    card/printing/product/price-row cardinality columns apply), and a new ``SyncKind``
    would force a CHECK-altering migration on the shared sync model. This is the
    valuation app's first model.

    Append-only (the ``SyncRun`` / ``PriceSnapshot`` posture): inserted, never updated;
    the admin blocks edit/delete. The deferred coverage-collapse alerting reads this
    alongside ``SyncRun``.
    """

    status = models.CharField(max_length=16, choices=ValuationStatus.choices)
    # Per-pass counts from ValuationResult, filled on SUCCESS. Left NULL on SKIPPED
    # (nothing was valued) and FAILED (the pass rolled back) -- the SyncRun pattern,
    # where a count that doesn't apply to an outcome stays NULL rather than a misleading 0.
    portfolios_seen = models.PositiveIntegerField(null=True, blank=True)
    snapshots_created = models.PositiveIntegerField(null=True, blank=True)
    snapshots_existing = models.PositiveIntegerField(null=True, blank=True)
    holdings_valued = models.PositiveIntegerField(null=True, blank=True)
    holdings_unpriced = models.PositiveIntegerField(null=True, blank=True)
    # The full ValuationResult asdict for audit. Not read by anything, so an open shape
    # with no CHECK (the SyncRun.detail precedent).
    detail = models.JSONField(default=dict, blank=True)
    # Why a run FAILED or was SKIPPED (blank on success).
    error = models.TextField(blank=True, default="")

    class Meta:
        # Most-recent-first; `-id` is a stable tiebreaker so "latest" is deterministic
        # even if two runs share a created_at (the CollectionLot ordering lesson).
        # No `kind` ordering -- this model is valuation-only.
        ordering = ["-created_at", "-id"]
        constraints = [
            # Closed-vocabulary guard at the DB (`choices` is form-layer only) -- the
            # SyncRun / PriceSnapshot enum-CHECK precedent; enforced on sqlite too.
            models.CheckConstraint(
                condition=models.Q(status__in=ValuationStatus.values),
                name="valuation_run_status_valid",
            ),
        ]
        indexes = [
            # "Has valuation succeeded recently" lookups (admin / the deferred alerting).
            models.Index(fields=["status", "-created_at"], name="valuation_run_status_idx"),
        ]

    def __str__(self) -> str:
        return f"Valuation {self.get_status_display()} ({self.created_at:%Y-%m-%d %H:%M})"
