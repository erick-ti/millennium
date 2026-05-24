from __future__ import annotations

from dataclasses import asdict
from typing import Any

import structlog
from django.conf import settings

from apps.core.locks import sync_lock
from apps.core.models import SyncKind, SyncStatus
from apps.core.sync_history import record_run, shrink_floor
from apps.pricing.ingestion import IngestResult, ingest_prices
from apps.pricing.providers.base import JsonFetcher, fetch_json
from apps.pricing.providers.tcgcsv import TcgcsvProvider
from apps.pricing.reconciliation import ReconcileResult, reconcile_products_to_printings

logger = structlog.get_logger(__name__)


def run_tcgcsv_sync(
    *, fetch: JsonFetcher = fetch_json
) -> tuple[ReconcileResult, IngestResult] | None:
    """Run the TCGCSV pipeline (reconcile → ingest) under the compare-to-previous
    cardinality guard, recording the outcome in ``SyncRun`` (DECISIONS 2026-05-24 slice 3).

    Reconcile must precede ingest so ``external_price_ids`` exist before pricing joins
    through them (DECISIONS 2026-05-23). The recurring-safety guard (round-4 prerequisite
    #2) raises the provider's product and price-row floors to ``last_good * (1 - tolerance)``
    once history exists, so a truncated fetch is rejected before writes; the first run
    uses the provider's absolute bootstrap floors. A SUCCESS row records both
    cardinalities (the next run's baseline); a failure (including a guard rejection or a
    misconfigured tolerance) records FAILED + the error and re-raises.

    Serialized by a per-kind advisory lock: reconciliation's per-group get-then-create
    paths assume a single writer, which beat alone doesn't enforce (e.g. a manual
    ``sync_tcgcsv`` overlapping the scheduled task could collide on ``external_price_ids``,
    aborting a run mid-way with partial commits). If another run holds the lock this one
    **skips** (logs and returns ``None`` -- no ``SyncRun``). The single entry point for the
    pipeline, called by both the management command and the Celery task. ``fetch`` is
    injectable for tests; one provider instance fetches the group list once, shared by
    ``fetch_products`` and ``fetch_prices``.
    """
    with sync_lock(SyncKind.TCGCSV_PRICING) as acquired:
        if not acquired:
            logger.warning("tcgcsv_sync.skipped_already_running")
            return None
        tolerance = settings.SYNC_GUARD_PRICING_TOLERANCE
        try:
            # shrink_floor inside the try so a misconfigured tolerance records FAILED too.
            provider = TcgcsvProvider(
                fetch,
                min_products=shrink_floor(
                    SyncKind.TCGCSV_PRICING, "product_count", tolerance=tolerance
                ),
                min_price_rows=shrink_floor(
                    SyncKind.TCGCSV_PRICING, "price_row_count", tolerance=tolerance
                ),
            )
            rec = reconcile_products_to_printings(provider.fetch_products())
            ing = ingest_prices(
                provider.fetch_prices(), excluded_external_ids=rec.conflicted_external_ids
            )
        except Exception as exc:
            record_run(SyncKind.TCGCSV_PRICING, SyncStatus.FAILED, error=str(exc))
            raise
        record_run(
            SyncKind.TCGCSV_PRICING,
            SyncStatus.SUCCESS,
            product_count=rec.products_seen,
            price_row_count=ing.prices_seen,
            detail={"reconcile": reconcile_detail(rec), "ingest": asdict(ing)},
        )
        return rec, ing


def reconcile_detail(rec: ReconcileResult) -> dict[str, Any]:
    # asdict leaves the frozenset intact, which JSONField can't serialize — render it
    # as a sorted list so the audit detail round-trips.
    data = asdict(rec)
    data["conflicted_external_ids"] = sorted(data["conflicted_external_ids"])
    return data
