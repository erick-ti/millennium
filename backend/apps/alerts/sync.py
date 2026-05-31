from __future__ import annotations

from dataclasses import asdict
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone

from apps.alerts.evaluation import AlertEvaluationResult, evaluate_active_rules
from apps.alerts.models import AlertRun, AlertStatus
from apps.core.locks import alerts_lock
from apps.core.models import SyncKind, SyncRun, SyncStatus

logger = structlog.get_logger(__name__)


def _pricing_succeeded_today() -> bool:
    """Whether a TCGCSV pricing sync recorded SUCCESS for the current UTC day.

    The dependency the alert evaluation refuses to run without — its price moves are read
    from ``price_snapshots`` (via ``compute_collection_movers``), so a same-day pricing
    success is what makes today's anchor meaningful. Mirrors valuation's identically-named
    gate (``apps/valuation/sync.py``); redefined here (rather than importing valuation's
    private helper) so the alerts app carries no dependency on valuation's orchestration.
    ``created_at__date`` truncates in the DB session timezone, pinned to UTC (USE_TZ +
    TIME_ZONE="UTC"), matching ``timezone.localdate()`` — the snapshot key's UTC day.
    """
    return SyncRun.objects.filter(
        kind=SyncKind.TCGCSV_PRICING,
        status=SyncStatus.SUCCESS,
        created_at__date=timezone.localdate(),
    ).exists()


def record_alert_run(
    status: AlertStatus,
    *,
    result: AlertEvaluationResult | None = None,
    error: str = "",
) -> AlertRun:
    """Append one ``AlertRun`` recording a pass's outcome. Counts come from ``result`` on
    SUCCESS; SKIPPED/FAILED pass no result, so the count columns stay NULL and the reason
    rides in ``error`` (the ``record_valuation_run`` shape)."""
    detail: dict[str, Any] = asdict(result) if result is not None else {}
    counts: dict[str, int] = (
        {"rules_evaluated": result.rules_evaluated, "events_created": result.events_created}
        if result is not None
        else {}
    )
    return AlertRun.objects.create(status=status, error=error, detail=detail, **counts)


def run_alerts() -> AlertEvaluationResult | None:
    """Evaluate active price-alert rules for today under a per-run advisory lock and a
    dependency on a successful same-day TCGCSV pricing run, recording an ``AlertRun``.
    The single entry point both the ``run_alerts`` management command and the Celery task
    call (the ``run_valuation`` orchestration pattern), so a manual run is equally guarded
    and recorded.

    Returns the ``AlertEvaluationResult`` on SUCCESS, or ``None`` when the run did not
    evaluate:

    - **Alerts lock held** by another run: logs and returns ``None``, recording *nothing*
      — the sibling holding the lock will record (the ``run_valuation`` lock-skip
      semantics). The only skip that records nothing, because the lock-holder covers the day.
    - **Pricing dependency unmet** (no successful TCGCSV pricing run today): logs, records
      a SKIPPED ``AlertRun``, returns ``None`` — a refused run is a real operational event
      worth auditing.

    The ``AlertEvent`` writes and the SUCCESS ``AlertRun`` commit in one
    ``transaction.atomic``, so the audit row can't desync from the events it records;
    FAILED is recorded after that rollback (outside the block) and the error re-raised.

    Unlike ``run_valuation``, this does NOT additionally exclude a concurrently-running
    pricing ingest (no nested pricing lock): alert events are an informational feed, not an
    irreversible per-day headline metric whose partial read permanently corrupts a time
    series, and the beat gap (pricing 03:00 → alerts 05:00) makes overlap unlikely. A
    partial read at worst defers one pair's alert by a day (the pair, still elevated, fires
    on the next day's window) — never a false positive. Re-run idempotency comes from the
    ``AlertEvent`` UNIQUE, so a lighter guard suffices.
    """
    with alerts_lock() as acquired:
        if not acquired:
            logger.warning("alerts.skipped_already_running")
            return None
        if not _pricing_succeeded_today():
            logger.warning("alerts.skipped_no_pricing_run")
            record_alert_run(
                AlertStatus.SKIPPED,
                error=(
                    "no successful TCGCSV pricing SyncRun recorded for today -- "
                    "run the pricing sync (sync_tcgcsv) first"
                ),
            )
            return None
        try:
            # Events + the SUCCESS run commit together: a failed run insert rolls the
            # events back too, so an append-only event is never orphaned without its audit
            # row. FAILED is recorded after the rollback, outside the block.
            with transaction.atomic():
                result = evaluate_active_rules()
                record_alert_run(AlertStatus.SUCCESS, result=result)
        except Exception as exc:
            record_alert_run(AlertStatus.FAILED, error=str(exc))
            raise
        return result
