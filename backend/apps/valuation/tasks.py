from __future__ import annotations

from dataclasses import asdict
from typing import Any

from celery import shared_task

from apps.valuation.sync import run_valuation


@shared_task(name="valuation.value_portfolios")
def value_portfolios() -> dict[str, Any]:
    """Celery entry point for the daily portfolio valuation (DECISIONS 2026-05-25 slice 4c).

    A thin wrapper: the advisory lock, the same-day pricing dependency, and the
    ``ValuationRun`` recording all live in ``run_valuation``, shared with the management
    command. Returns the per-run counts for the result backend, or ``{"skipped": True}``
    when the run did not value (lock held, or no successful TCGCSV pricing run today --
    the ``ValuationRun`` history records which). Beat-scheduled daily at 04:00 UTC, after
    the pricing sync.
    """
    result = run_valuation()
    if result is None:
        return {"skipped": True}
    return asdict(result)
