from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.pricing.sync import run_tcgcsv_sync


class Command(BaseCommand):
    help = "Reconcile TCGCSV products to printings, then ingest current single-card prices."

    def handle(self, *args: Any, **options: Any) -> None:
        # Reconcile-then-ingest under the compare-to-previous cardinality guard, recording
        # a SyncRun (DECISIONS 2026-05-24 slice 3) — same orchestration the Celery task uses.
        outcome = run_tcgcsv_sync()
        if outcome is None:
            # Another run held the advisory lock — this invocation was skipped.
            self.stdout.write(
                self.style.WARNING("TCGCSV sync skipped: another run is already in progress.")
            )
            return
        rec, ing = outcome
        queued = (
            rec.queued_no_printing_match
            + rec.queued_multi_variant
            + rec.queued_rarity_disagreement
            + rec.queued_external_id_conflict
        )
        self.stdout.write(
            self.style.SUCCESS(
                "TCGCSV sync complete: "
                f"products {rec.exact_matched} exact-matched / {rec.rarity_reconciled} "
                f"rarity-reconciled, {rec.external_ids_created} external ids written, "
                f"{queued} queued for review; "
                f"prices {ing.snapshots_created} snapshots written / "
                f"{ing.snapshots_existing} already present, "
                f"{ing.skipped_unmatched_product} unmatched + "
                f"{ing.skipped_conflicted_product} conflicted + "
                f"{ing.skipped_unknown_subtype} unknown-subtype + "
                f"{ing.skipped_no_price} no-price skipped"
            )
        )
