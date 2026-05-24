from __future__ import annotations

from dataclasses import asdict
from typing import Any

from celery import shared_task

from apps.pricing.sync import reconcile_detail, run_tcgcsv_sync


@shared_task(name="pricing.sync_tcgcsv_pricing")
def sync_tcgcsv_pricing() -> dict[str, Any]:
    """Celery entry point for the daily TCGCSV pricing pipeline (DECISIONS 2026-05-24
    slice 3).

    A thin wrapper: the cardinality guard and the ``SyncRun`` recording live in
    ``run_tcgcsv_sync``, shared with the management command. Returns the per-run counts
    for the Celery result backend — via ``reconcile_detail`` so the reconcile result's
    ``conflicted_external_ids`` frozenset is rendered JSON-serializable — or
    ``{"skipped": True}`` when the advisory lock was held by another run. Beat-scheduled
    daily via ``CELERY_BEAT_SCHEDULE`` (after the metadata sync).
    """
    outcome = run_tcgcsv_sync()
    if outcome is None:
        return {"skipped": True}
    rec, ing = outcome
    return {"reconcile": reconcile_detail(rec), "ingest": asdict(ing)}
