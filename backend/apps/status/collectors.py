"""Internal status tier — pure local DB reads, deliberately NOT cached.

This is the heart of the dashboard (the live pipeline flow + app state), so it stays
as live as a normal query — only the external provider tiers (Hetzner, Healthchecks)
are cached to spare their rate limits. Everything here reads the run-history tables the
nightly systemd timers already write (``SyncRun`` / ``ValuationRun`` / ``AlertRun``),
so "is today's chain green?" is a DB query, not a host call (the backend container can't
read host ``systemctl``/``docker`` anyway).
"""

from __future__ import annotations

import time
from datetime import date
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.alerts.models import AlertRun
from apps.cards.models import Card, CardPrinting
from apps.collection.models import CollectionItem, CollectionLot
from apps.core.models import SyncKind, SyncRun
from apps.core.sync_history import last_successful_count
from apps.portfolio.models import Portfolio, PortfolioValueSnapshot
from apps.pricing.models import PriceSnapshot
from apps.valuation.models import ValuationRun

# Captured once per worker at import — uptime is per-worker (WEB_CONCURRENCY starts N
# workers, each with its own clock), an approximate "how long has this server been up",
# not a cluster figure. monotonic so a wall-clock adjustment can't make it go backwards.
_PROCESS_STARTED = time.monotonic()

# A pipeline-flow node's status light.
_GREEN, _AMBER, _RED, _GREY = "green", "amber", "red", "grey"
# Run-status string values, shared across SyncStatus / ValuationStatus / AlertStatus
# (all three use "success"/"failed"; valuation+alerts add "skipped" → amber).
_SUCCESS, _FAILED = "success", "failed"


def _derive_stage(
    *,
    key: str,
    label: str,
    scheduled_utc: str,
    metric_label: str,
    depends_on: str | None,
    latest: Any,
    metric_field: str,
    today: date,
    sync_kind: SyncKind | None = None,
) -> dict[str, Any]:
    """Shape one pipeline-flow node from its latest run row (or None).

    status: grey (never ran) / red (ran today + FAILED) / amber (ran today + SKIPPED,
    or a stale run from a prior day) / green (ran today + SUCCESS). ``green_today`` is
    the SUCCESS-today predicate the flow's light reads. For the SyncRun stages a latest
    run with a NULL count (e.g. a pre-fetch failure) falls back to the last-good
    high-water mark so the tile isn't blank.
    """
    if latest is None:
        metric_value = None
    else:
        metric_value = getattr(latest, metric_field, None)
        if metric_value is None and sync_kind is not None:
            metric_value = last_successful_count(sync_kind, metric_field)

    if latest is None:
        status, last_run_at, green_today = _GREY, None, False
    else:
        ran_today = timezone.localdate(latest.created_at) == today
        last_run_at = latest.created_at
        green_today = ran_today and latest.status == _SUCCESS
        if not ran_today:
            status = _AMBER  # stale — the chain didn't run (or hasn't yet) today
        elif latest.status == _SUCCESS:
            status = _GREEN
        elif latest.status == _FAILED:
            status = _RED
        else:  # skipped (valuation/alerts refused on the same-day-pricing gate)
            status = _AMBER

    return {
        "key": key,
        "label": label,
        "scheduled_utc": scheduled_utc,
        "status": status,
        "last_run_at": last_run_at,
        "green_today": green_today,
        "metric_label": metric_label,
        "metric_value": metric_value,
        "depends_on": depends_on,
    }


def _pipeline_stages(today: date) -> list[dict[str, Any]]:
    """The ordered nightly chain — the flow's data. metadata → pricing → {valuation,
    alerts}; the depends_on edges encode the real same-day-pricing gate that valuation
    and alerts refuse without (so the flow shows where the chain would break). Backup +
    CD stages are appended client-side from /api/status/checks/ (slice 2)."""
    metadata_latest = (
        SyncRun.objects.filter(kind=SyncKind.YGOPRODECK_METADATA)
        .order_by("-created_at", "-id")
        .first()
    )
    pricing_latest = (
        SyncRun.objects.filter(kind=SyncKind.TCGCSV_PRICING)
        .order_by("-created_at", "-id")
        .first()
    )
    valuation_latest = ValuationRun.objects.order_by("-created_at", "-id").first()
    alerts_latest = AlertRun.objects.order_by("-created_at", "-id").first()
    return [
        _derive_stage(
            key="metadata",
            label="Metadata sync",
            scheduled_utc="02:00",
            metric_label="cards",
            depends_on=None,
            latest=metadata_latest,
            metric_field="card_count",
            today=today,
            sync_kind=SyncKind.YGOPRODECK_METADATA,
        ),
        _derive_stage(
            key="pricing",
            label="Pricing sync",
            scheduled_utc="03:00",
            metric_label="prices",
            depends_on=None,
            latest=pricing_latest,
            metric_field="price_row_count",
            today=today,
            sync_kind=SyncKind.TCGCSV_PRICING,
        ),
        _derive_stage(
            key="valuation",
            label="Valuation",
            scheduled_utc="04:00",
            metric_label="holdings valued",
            depends_on="pricing",
            latest=valuation_latest,
            metric_field="holdings_valued",
            today=today,
        ),
        _derive_stage(
            key="alerts",
            label="Alerts",
            scheduled_utc="05:00",
            metric_label="events",
            depends_on="pricing",
            latest=alerts_latest,
            metric_field="events_created",
            today=today,
        ),
    ]


def _catalog() -> dict[str, Any]:
    """The state the pipeline maintains. owned_holdings excludes zero-quantity
    (catalogued-but-not-held) items — the movers ``_owned_pairs`` qty>0 rule."""
    owned = CollectionItem.objects.annotate(
        qty=Coalesce(Sum("lots__quantity"), 0)
    ).filter(qty__gt=0)
    return {
        "cards": Card.objects.count(),
        "printings": CardPrinting.objects.count(),
        "price_snapshots": PriceSnapshot.objects.count(),
        "portfolios": Portfolio.objects.count(),
        "owned_holdings": owned.count(),
        "owned_copies": CollectionLot.objects.aggregate(
            total=Coalesce(Sum("quantity"), 0)
        )["total"],
    }


def _latest_valuation() -> dict[str, Any]:
    """The most-recent day's portfolio value. ``complete`` is True only when EVERY
    portfolio's snapshot that day is fully covered — partial coverage sums a subset, so
    a value with complete=False must not read as a true total (the never-coerce-unknowns
    -to-zero rule, DECISIONS 2026-05-25). NULL-safe when nothing has been valued yet."""
    latest = PortfolioValueSnapshot.objects.order_by("-snapshot_date").first()
    if latest is None:
        return {
            "as_of": None,
            "market_value": None,
            "complete": None,
            "portfolios_valued": 0,
        }
    same_day = list(
        PortfolioValueSnapshot.objects.filter(snapshot_date=latest.snapshot_date)
    )
    total = Decimal("0")
    for snap in same_day:
        total += snap.market_value
    return {
        "as_of": latest.snapshot_date,
        "market_value": total,
        "complete": all(snap.is_complete for snap in same_day),
        "portfolios_valued": len(same_day),
    }


def _recent_runs() -> list[dict[str, Any]]:
    """The last 14 sync runs (newest first) — the cardinality trend the flow charts."""
    return [
        {
            "kind": run.kind,
            "status": run.status,
            "created_at": run.created_at,
            "card_count": run.card_count,
            "printing_count": run.printing_count,
            "product_count": run.product_count,
            "price_row_count": run.price_row_count,
        }
        for run in SyncRun.objects.order_by("-created_at", "-id")[:14]
    ]


def _app_meta() -> dict[str, Any]:
    return {
        "version": settings.GIT_SHA,
        "environment": settings.SETTINGS_MODULE.rsplit(".", 1)[-1],
        "server_time": timezone.now(),
        "uptime_seconds": int(time.monotonic() - _PROCESS_STARTED),
    }


def build_overview() -> dict[str, Any]:
    """The internal status tier as one dict — fed straight to ``StatusOverviewSerializer``."""
    today = timezone.localdate()
    return {
        "app": _app_meta(),
        "pipeline": _pipeline_stages(today),
        "catalog": _catalog(),
        "valuation": _latest_valuation(),
        "recent_runs": _recent_runs(),
    }
