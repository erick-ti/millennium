from __future__ import annotations

from dataclasses import asdict
from typing import Any

from celery import shared_task

from apps.alerts.sync import run_alerts


@shared_task(name="alerts.compute_alerts")
def compute_alerts() -> dict[str, Any]:
    """Celery entry point for the daily price-alert evaluation (Phase 5 slice 4).

    A thin wrapper: the advisory lock, the same-day pricing dependency, and the
    ``AlertRun`` recording all live in ``run_alerts``, shared with the management command.
    Returns the per-run counts for the result backend, or ``{"skipped": True}`` when the
    run did not evaluate (lock held, or no successful TCGCSV pricing run today -- the
    ``AlertRun`` history records which). Beat-scheduled daily at 05:00 UTC, after pricing
    (03:00) and valuation (04:00).
    """
    result = run_alerts()
    if result is None:
        return {"skipped": True}
    return asdict(result)
