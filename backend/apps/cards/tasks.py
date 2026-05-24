from __future__ import annotations

from dataclasses import asdict
from typing import Any

from celery import shared_task

from apps.cards.sync import run_ygoprodeck_sync


@shared_task(name="cards.sync_ygoprodeck_metadata")
def sync_ygoprodeck_metadata() -> dict[str, Any]:
    """Celery entry point for the daily YGOPRODeck metadata sync (DECISIONS 2026-05-24
    slice 3).

    A thin wrapper: the cardinality guard and the ``SyncRun`` recording live in
    ``run_ygoprodeck_sync``, shared with the management command. Returns the per-run
    counts (all ints, JSON-serializable) for the Celery result backend, or
    ``{"skipped": True}`` when the advisory lock was held by another run. Beat-scheduled
    daily via ``CELERY_BEAT_SCHEDULE``.
    """
    result = run_ygoprodeck_sync()
    if result is None:
        return {"skipped": True}
    return asdict(result)
