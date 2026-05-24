from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.pricing.ingestion import ingest_prices
from apps.pricing.providers.tcgcsv import TcgcsvProvider
from apps.pricing.reconciliation import reconcile_products_to_printings


class Command(BaseCommand):
    help = "Reconcile TCGCSV products to printings, then ingest current single-card prices."

    def handle(self, *args: Any, **options: Any) -> None:
        # Reconcile first so external_price_ids exist before pricing joins through them
        # (DECISIONS 2026-05-23). One provider instance fetches the group list once.
        provider = TcgcsvProvider()
        rec = reconcile_products_to_printings(provider.fetch_products())
        # Pass the run's conflicted ids so ingestion skips pricing through stale mappings.
        ing = ingest_prices(
            provider.fetch_prices(), excluded_external_ids=rec.conflicted_external_ids
        )
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
