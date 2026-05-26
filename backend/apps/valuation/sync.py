from __future__ import annotations

from dataclasses import asdict
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone

from apps.core.locks import sync_lock, valuation_lock
from apps.core.models import SyncKind, SyncRun, SyncStatus
from apps.valuation.engine import ValuationResult, value_all_portfolios
from apps.valuation.models import ValuationRun, ValuationStatus

logger = structlog.get_logger(__name__)


def _pricing_succeeded_today() -> bool:
    """Whether a TCGCSV pricing sync recorded SUCCESS for the current UTC day.

    The hard dependency the valuation orchestration refuses to run without (DECISIONS
    2026-05-25 slice 4c). Pricing's ingest commits ``PriceSnapshot`` rows incrementally,
    so valuing before today's pricing run has *succeeded* could roll a mixed/stale price
    set into the day's unique, delete-blocked snapshot -- uncorrectable. The
    03:00->04:00 beat gap alone is not enough (a slow ingest could overrun 04:00), so
    this checks the recorded run, not the clock. ``created_at__date`` truncates in the
    DB's session timezone, which Django pins to UTC (USE_TZ + TIME_ZONE="UTC"), matching
    ``timezone.localdate()`` -- the same UTC-day discipline the snapshot key uses.
    """
    return SyncRun.objects.filter(
        kind=SyncKind.TCGCSV_PRICING,
        status=SyncStatus.SUCCESS,
        created_at__date=timezone.localdate(),
    ).exists()


def record_valuation_run(
    status: ValuationStatus,
    *,
    result: ValuationResult | None = None,
    error: str = "",
) -> ValuationRun:
    """Append one ``ValuationRun`` recording a pass's outcome. Counts come from
    ``result`` on SUCCESS; SKIPPED/FAILED pass no result, so the count columns stay NULL
    and the reason rides in ``error`` (the ``record_run`` shape for ``SyncRun``)."""
    detail: dict[str, Any] = asdict(result) if result is not None else {}
    counts: dict[str, int] = (
        {
            "portfolios_seen": result.portfolios_seen,
            "snapshots_created": result.snapshots_created,
            "snapshots_existing": result.snapshots_existing,
            "holdings_valued": result.holdings_valued,
            "holdings_unpriced": result.holdings_unpriced,
        }
        if result is not None
        else {}
    )
    return ValuationRun.objects.create(status=status, error=error, detail=detail, **counts)


def run_valuation() -> ValuationResult | None:
    """Value every portfolio for today under a per-run advisory lock and a hard
    dependency on a successful same-day TCGCSV pricing run, recording a ``ValuationRun``
    (DECISIONS 2026-05-25 slice 4c). The single entry point both the ``value_portfolios``
    management command and the Celery task call, so a manual run is equally guarded and
    recorded -- the ``run_tcgcsv_sync`` orchestration pattern.

    Returns the ``ValuationResult`` on SUCCESS, or ``None`` when the run did not value:

    - **Valuation lock held** by another valuation run: logs and returns ``None``,
      recording *nothing* -- the sibling holding the lock will record, so a redundant
      concurrent invocation isn't its own history row (the ``run_tcgcsv_sync`` skip
      semantics). This is the only skip that records nothing, because the lock-holder
      covers the day.
    - **Pricing dependency unmet** (no successful TCGCSV pricing run today): logs,
      records a SKIPPED ``ValuationRun``, returns ``None``. Unlike a redundant
      invocation, a refused run means *no* valuation happened today -- a real operational
      event worth auditing.
    - **Pricing currently in progress** (a same-day success exists, but a pricing run
      holds the pricing lock right now -- e.g. a manual rerun): logs, records SKIPPED,
      returns ``None``. A same-day SUCCESS proves pricing finished *once*, not that it
      isn't appending rows again, so valuing mid-run would read a partial price table
      into the irreversible daily snapshot (adversarial review 2026-05-25, finding 1).

    The snapshot writes and the SUCCESS ``ValuationRun`` commit in one outer
    ``transaction.atomic`` (the engine's own per-pass atomic nests as a savepoint), so the
    audit row can never desync from the irreversible snapshots it records -- a failure
    anywhere, including the run insert itself, rolls back the whole pass. FAILED is then
    recorded after that rollback (outside the block) and the error re-raised.

    There is deliberately NO fetch-floor guard (contrast ``run_tcgcsv_sync``): valuation
    reads local data, so there is no fetch and nothing to truncate.
    """
    with valuation_lock() as acquired:
        if not acquired:
            logger.warning("valuation.skipped_already_running")
            return None
        if not _pricing_succeeded_today():
            logger.warning("valuation.skipped_no_pricing_run")
            record_valuation_run(
                ValuationStatus.SKIPPED,
                error=(
                    "no successful TCGCSV pricing SyncRun recorded for today -- "
                    "run the pricing sync (sync_tcgcsv) first"
                ),
            )
            return None
        # A same-day SUCCESS proves pricing finished at least once, not that it isn't
        # running *again* right now: a manual/duplicate sync_tcgcsv after that success
        # holds the pricing lock and keeps appending today's PriceSnapshot rows
        # (get_or_create, so existing prices are never overwritten -- but newly-matched
        # printings land mid-run). Valuing then would read a partially-committed price
        # table into the day's unique, delete-blocked snapshot, understating coverage
        # irreversibly. So gate on the pricing lock too: skip if a pricing run is active,
        # otherwise hold it across the price-map read + snapshot write so none starts
        # mid-valuation (adversarial review 2026-05-25, finding 1). No deadlock -- pricing
        # never takes the valuation lock, so the two locks are only ever nested here.
        with sync_lock(SyncKind.TCGCSV_PRICING) as pricing_idle:
            if not pricing_idle:
                logger.warning("valuation.skipped_pricing_in_progress")
                record_valuation_run(
                    ValuationStatus.SKIPPED,
                    error=(
                        "a TCGCSV pricing run is currently in progress -- "
                        "retry after it completes"
                    ),
                )
                return None
            try:
                # Snapshots + the SUCCESS run commit together: value_all_portfolios's own
                # transaction.atomic nests as a savepoint under this outer one, so a failed
                # run insert rolls the snapshots back too. Without this the snapshot tx
                # commits on return, and a crash before record_valuation_run would orphan an
                # append-only, delete-blocked snapshot with no audit row -- which a retry's
                # get_or_create would then misreport as "0 created" (adversarial review
                # 2026-05-25, finding: snapshot/run atomicity). FAILED is recorded after the
                # rollback, outside the block.
                with transaction.atomic():
                    result = value_all_portfolios()
                    record_valuation_run(ValuationStatus.SUCCESS, result=result)
            except Exception as exc:
                record_valuation_run(ValuationStatus.FAILED, error=str(exc))
                raise
            return result
