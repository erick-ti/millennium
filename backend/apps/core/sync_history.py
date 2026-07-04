from __future__ import annotations

from typing import Any

from apps.core.models import SyncKind, SyncRun, SyncStatus


def last_successful_count(kind: SyncKind, dimension: str) -> int | None:
    """The fetch cardinality of the most recent SUCCESS run of ``kind`` for ``dimension``.

    ``dimension`` is a count field name on ``SyncRun`` (``card_count``,
    ``product_count``, ...). Returns ``None`` when no prior successful run carries that
    dimension, the first-run case, where the caller leaves the provider's own absolute
    bootstrap floor in force instead of a compare-to-previous one. Only SUCCESS rows are
    considered, so a rejected (FAILED) run never becomes the baseline.
    """
    run = (
        SyncRun.objects.filter(
            kind=kind, status=SyncStatus.SUCCESS, **{f"{dimension}__isnull": False}
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if run is None:
        return None
    count: int = getattr(run, dimension)
    return count


def shrink_floor(kind: SyncKind, dimension: str, *, tolerance: float) -> int | None:
    """The compare-to-previous fetch floor for ``dimension``: ``last_good * (1 - tolerance)``.

    This is the rerun-safety guard: catalogs only
    grow (Konami never un-releases; the TCGCSV catalog only expands), so the floor
    tracks the last-good high-water mark and a fetch below it is a likely truncation,
    rejected before any write. Returns ``None`` on the first run (no history); the
    caller then passes ``None`` to the provider, which falls back to its absolute
    bootstrap floor. ``tolerance`` is the allowed downward drift (e.g. ``0.02`` = 2%).

    Fail closed on a misconfigured ``tolerance`` (a settings knob): it MUST be a fraction
    in ``[0, 1)``. A value >= 1 yields a non-positive floor that no fetch can fall below,
    silently disabling the guard (and bypassing the bootstrap floor too) so a truncated
    dump records as SUCCESS and ratchets the baseline down; a value < 0 yields a floor
    above the last-good count, bricking every run. Both are the percent-vs-fraction class
    of operator error (``=2`` meaning "2%"), so we raise rather than compute a dangerous
    floor -- the project's fail-closed posture (the same reason the baseline is a durable
    model, not a fail-open cache).
    """
    if not 0 <= tolerance < 1:
        raise ValueError(
            f"sync guard tolerance must be a fraction in [0, 1) (e.g. 0.10 for 10%), "
            f"got {tolerance!r} -- refusing to compute a floor that would disable the "
            f"truncation guard (tolerance >= 1) or brick the sync (tolerance < 0)."
        )
    last = last_successful_count(kind, dimension)
    if last is None:
        return None
    return int(last * (1 - tolerance))


def record_run(
    kind: SyncKind,
    status: SyncStatus,
    *,
    card_count: int | None = None,
    printing_count: int | None = None,
    product_count: int | None = None,
    price_row_count: int | None = None,
    detail: dict[str, Any] | None = None,
    error: str = "",
) -> SyncRun:
    """Append one ``SyncRun`` recording a completed run's outcome and cardinality.

    The dimensions left at ``None`` are those that don't apply to ``kind`` (or are
    unknown after a pre-fetch failure). ``detail`` is the full per-run counts for audit.
    """
    return SyncRun.objects.create(
        kind=kind,
        status=status,
        card_count=card_count,
        printing_count=printing_count,
        product_count=product_count,
        price_row_count=price_row_count,
        detail=detail or {},
        error=error,
    )
