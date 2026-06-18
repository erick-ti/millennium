"""Persist one host-metric sample read as JSON from stdin.

The host-side collector (``infra/hetzner/collect_host_metrics.py``) reads ``/proc`` +
``statvfs`` and pipes a JSON object to this command running INSIDE the backend
container (``docker compose run --rm --no-deps -T backend python manage.py
record_host_metrics`` — a one-off container, NOT exec'd into the mem-capped live
backend; see record_host_metrics.sh).
The host can read host load; the container can write its own DB — together they bridge
the container isolation without a host mount or a docker socket.

Validates explicitly (no blind ``**payload`` into the model) so a malformed/garbage
sample fails loudly and is dropped rather than charting nonsense, then prunes samples
past the rolling retention window so the table stays bounded without a separate job.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.status.models import HostMetricSample

# The timer writes ~720 rows/day; the dashboard only charts a short trailing series, so
# keep a week and let the writer prune (no separate cleanup job).
_RETENTION_DAYS = 7

# Required fields + their coercers. Explicit (not **payload) so an unexpected key can't
# reach the model and a missing one fails loudly instead of persisting a half-sample.
_FIELDS: dict[str, Callable[[Any], Any]] = {
    "cpu_percent": float,
    "load_1m": float,
    "mem_used_mb": int,
    "mem_total_mb": int,
    "disk_used_gb": float,
    "disk_total_gb": float,
    "net_rx_bytes": int,
    "net_tx_bytes": int,
}


class Command(BaseCommand):
    help = "Persist one host-metric sample read as JSON from stdin (the host collector pipes it in)."

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            payload = json.loads(sys.stdin.read())
        except json.JSONDecodeError as exc:
            raise CommandError(f"invalid JSON on stdin: {exc}") from exc
        if not isinstance(payload, dict):
            raise CommandError("expected a JSON object on stdin")

        values: dict[str, Any] = {}
        for name, coerce in _FIELDS.items():
            if name not in payload:
                raise CommandError(f"missing field: {name}")
            try:
                values[name] = coerce(payload[name])
            except (TypeError, ValueError) as exc:
                raise CommandError(
                    f"field {name!r} is not a {coerce.__name__}: {exc}"
                ) from exc

        # Reject physically-impossible values rather than charting garbage.
        if not 0 <= values["cpu_percent"] <= 100:
            raise CommandError(f"cpu_percent out of range: {values['cpu_percent']}")
        if values["mem_total_mb"] <= 0 or values["disk_total_gb"] <= 0:
            raise CommandError("mem_total_mb / disk_total_gb must be positive")
        if any(
            values[k] < 0
            for k in ("mem_used_mb", "load_1m", "net_rx_bytes", "net_tx_bytes")
        ):
            raise CommandError("usage / counters must be non-negative")

        HostMetricSample.objects.create(**values)
        # Recording the sample is the unit's job; pruning is best-effort housekeeping.
        # Keep it off the failure path so a transient prune error (lock/timeout) doesn't
        # mark the oneshot FAILED for a run that actually recorded — the next tick's
        # prune clears any backlog.
        try:
            cutoff = timezone.now() - timedelta(days=_RETENTION_DAYS)
            deleted, _ = HostMetricSample.objects.filter(created_at__lt=cutoff).delete()
            self.stdout.write(f"recorded host sample (pruned {deleted} expired)")
        except Exception as exc:  # housekeeping must not fail the write
            self.stdout.write(f"recorded host sample (prune skipped: {exc})")
