from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection

from apps.core.models import SyncKind

# A fixed namespace for Millennium's two-int advisory-lock keys (the int4 pair form of
# pg_try_advisory_lock), so our lock ids can't collide with any other component's
# single-bigint advisory locks. Arbitrary constant ("MLN").
_ADVISORY_LOCK_NAMESPACE = 0x4D4C4E

# Stable advisory-lock id per recurring sync. The reconcile/ingest/metadata code paths
# document "single-writer -- no row locking", which beat scheduling alone does not
# enforce (a manual `sync_*` command overlapping the scheduled task, say); this lock
# serializes invocations of the same sync so the get-then-create paths can't race
# (DECISIONS 2026-05-24 slice 3 adversarial-review follow-up).
_SYNC_LOCK_IDS: dict[SyncKind, int] = {
    SyncKind.YGOPRODECK_METADATA: 1,
    SyncKind.TCGCSV_PRICING: 2,
}

# Valuation isn't a SyncKind -- it does no fetch and records its own ValuationRun rather
# than a SyncRun (DECISIONS 2026-05-25 slice 4c) -- so it takes its own id in the shared
# advisory namespace beside the sync ids (1 = metadata, 2 = pricing, 3 = valuation). The
# lock serializes valuation passes so a manual `value_portfolios` can't race the scheduled
# task on the same-day get_or_create snapshot path.
_VALUATION_LOCK_ID = 3

# Alerts (Phase 5) isn't a SyncKind either -- it does no fetch and records its own AlertRun
# -- so it takes the next id beside the others (1 = metadata, 2 = pricing, 3 = valuation,
# 4 = alerts). The lock serializes alert-evaluation passes so a manual `run_alerts` can't
# race the scheduled task on the same-day get_or_create AlertEvent path.
_ALERTS_LOCK_ID = 4


@contextmanager
def advisory_lock(lock_id: int) -> Iterator[bool]:
    """Best-effort cross-process mutual exclusion via a Postgres *session* advisory lock.

    Yields ``True`` if the lock was acquired (proceed) or ``False`` if another holder has
    it (skip). Non-blocking (``pg_try_advisory_lock``). The lock is session-scoped, so it
    is held across the sync's many per-group transactions (a transaction-scoped lock would
    drop at the first commit) and is released both on context exit and automatically by
    Postgres if the connection drops -- a crashed worker frees it with no TTL to tune
    (unlike a cache-expiry lock, whose timeout is the percent-vs-fraction class of footgun
    this design avoids elsewhere).

    On non-Postgres backends (sqlite under ``make test``) there are no advisory locks, so
    this yields ``True`` (runs unlocked); the real mutual exclusion is a Postgres runtime
    guarantee exercised by dev/prod/CI, the project's standard Postgres-only pattern
    (DECISIONS 2026-05-21). Same-session re-entry would re-acquire (advisory locks are
    re-entrant per session), but each sync runs in its own worker process/connection, so
    contention is always cross-connection.
    """
    if connection.vendor != "postgresql":
        yield True
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [_ADVISORY_LOCK_NAMESPACE, lock_id])
        acquired = bool(cursor.fetchone()[0])
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            cursor.execute(
                "SELECT pg_advisory_unlock(%s, %s)", [_ADVISORY_LOCK_NAMESPACE, lock_id]
            )


@contextmanager
def sync_lock(kind: SyncKind) -> Iterator[bool]:
    """``advisory_lock`` keyed by sync ``kind`` -- serializes runs of the same sync."""
    with advisory_lock(_SYNC_LOCK_IDS[kind]) as acquired:
        yield acquired


@contextmanager
def valuation_lock() -> Iterator[bool]:
    """``advisory_lock`` for the valuation pass -- serializes concurrent valuations."""
    with advisory_lock(_VALUATION_LOCK_ID) as acquired:
        yield acquired


@contextmanager
def alerts_lock() -> Iterator[bool]:
    """``advisory_lock`` for the alert-evaluation pass -- serializes concurrent runs."""
    with advisory_lock(_ALERTS_LOCK_ID) as acquired:
        yield acquired
