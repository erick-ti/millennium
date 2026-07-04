from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class HostMetricSample(TimeStampedModel):
    """One point-in-time sample of the host box's load.

    Written by the ``millennium-host-metrics`` systemd timer (host side) via the
    ``record_host_metrics`` management command (container side). The backend container
    is isolated (it can't read host ``/proc`` or the host disk), so a host-side
    collector reads ``/proc`` + ``statvfs`` and pipes a JSON sample to the command,
    which persists it here; ``/api/status/infra/`` then serves the latest sample plus a
    short trailing series (the sparkline). This is the same timer→Postgres→backend
    pattern the rest of the pipeline uses, with no external token and no host access
    from the container.

    Disposable telemetry, not an auditable record: the writer prunes to a rolling
    window each run, so these are NOT admin-locked like the append-only price/value
    snapshots.
    """

    cpu_percent = models.FloatField()  # whole-box CPU utilisation, 0-100
    load_1m = models.FloatField()  # /proc/loadavg 1-minute average
    mem_used_mb = models.PositiveIntegerField()
    mem_total_mb = models.PositiveIntegerField()
    disk_used_gb = models.FloatField()
    disk_total_gb = models.FloatField()
    # Cumulative bytes since boot (/proc/net/dev). The API derives a throughput rate
    # from the delta between the two most-recent samples rather than storing a rate, so
    # the collector stays a single stateless read (BigInteger: cumulative byte counters
    # blow past a 32-bit int within hours).
    net_rx_bytes = models.BigIntegerField()
    net_tx_bytes = models.BigIntegerField()

    class Meta:
        # The -id tiebreaker gives a deterministic "latest" for co-timestamped rows
        # (the SyncRun convention).
        ordering = ["-created_at", "-id"]
        # The infra endpoint only ever reads the newest N rows; this index serves that
        # ordered read and the prune-by-age delete.
        indexes = [
            models.Index(fields=["-created_at", "-id"], name="host_metric_recent_idx")
        ]

    def __str__(self) -> str:
        return f"host @ {self.created_at:%Y-%m-%d %H:%M} - cpu {self.cpu_percent:.0f}%"
