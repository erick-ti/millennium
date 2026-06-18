from __future__ import annotations

from typing import Any

from rest_framework import serializers


class AppMetaSerializer(serializers.Serializer[dict[str, Any]]):
    version = serializers.CharField()
    environment = serializers.CharField()
    server_time = serializers.DateTimeField()
    uptime_seconds = serializers.IntegerField()


class PipelineStageSerializer(serializers.Serializer[dict[str, Any]]):
    key = serializers.CharField()
    # DRF's metaclass moves declared fields into _declared_fields, so this doesn't
    # actually shadow Field.label at runtime — mypy just can't see the metaclass.
    label = serializers.CharField()  # type: ignore[assignment]
    scheduled_utc = serializers.CharField()
    status = serializers.CharField()
    last_run_at = serializers.DateTimeField(allow_null=True)
    green_today = serializers.BooleanField()
    metric_label = serializers.CharField()
    metric_value = serializers.IntegerField(allow_null=True)
    depends_on = serializers.CharField(allow_null=True)


class CatalogSerializer(serializers.Serializer[dict[str, Any]]):
    cards = serializers.IntegerField()
    printings = serializers.IntegerField()
    price_snapshots = serializers.IntegerField()
    portfolios = serializers.IntegerField()
    owned_holdings = serializers.IntegerField()
    owned_copies = serializers.IntegerField()


class ValuationSummarySerializer(serializers.Serializer[dict[str, Any]]):
    as_of = serializers.DateField(allow_null=True)
    # String-serialized Decimal (PriceSnapshotSerializer precedent), so the frontend's
    # parseDecimal keeps NULL ("not yet valued") distinct from 0. max_digits 16 (not the
    # per-row 14): this is the cross-portfolio SUM of each portfolio's market_value, which
    # can exceed any single row's width — DRF's DecimalField raises (→ 500) if
    # to_representation overflows max_digits, so the sum needs headroom over the row cap.
    market_value = serializers.DecimalField(
        max_digits=16, decimal_places=2, allow_null=True
    )
    complete = serializers.BooleanField(allow_null=True)
    portfolios_valued = serializers.IntegerField()


class RecentRunSerializer(serializers.Serializer[dict[str, Any]]):
    kind = serializers.CharField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    card_count = serializers.IntegerField(allow_null=True)
    printing_count = serializers.IntegerField(allow_null=True)
    product_count = serializers.IntegerField(allow_null=True)
    price_row_count = serializers.IntegerField(allow_null=True)


class CheckRowSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField()
    # Healthchecks status vocabulary: up / down / grace / paused / new.
    status = serializers.CharField()
    # Passthrough ISO datetime string from the provider (the frontend formats it) —
    # CharField, not DateTimeField, so DRF doesn't try to re-parse an external string.
    last_ping_at = serializers.CharField(allow_null=True)
    n_pings = serializers.IntegerField()


class ChecksStatusSerializer(serializers.Serializer[dict[str, Any]]):
    """The Healthchecks tier — the backup + CD dead-man checks for the flow's trailing
    nodes. ``configured`` false = no read-API key; ``available`` false = a provider
    error (the tile degrades, never 500s). backup/cd are null when unconfigured,
    unavailable, or their slug didn't match a check."""

    configured = serializers.BooleanField()
    available = serializers.BooleanField()
    error = serializers.CharField(allow_null=True)
    backup = CheckRowSerializer(allow_null=True)
    cd = CheckRowSerializer(allow_null=True)


class InfraStatusSerializer(serializers.Serializer[dict[str, Any]]):
    """The host-box tier — CPU/mem/disk/load + a CPU sparkline, from the samples the
    host collector writes (NOT a host call — the container can't read host /proc). No
    external token, so no ``configured`` flag: ``available`` is whether a RECENT sample
    exists, ``stale`` whether the latest is past the freshness window. Every value is
    nullable — the no-sample state (dev, or before the first timer tick) returns
    all-null, which the tile renders as "awaiting host metrics" (a plain Serializer
    doesn't auto-detect nullability, so each ``allow_null`` is set by hand)."""

    available = serializers.BooleanField()
    stale = serializers.BooleanField()
    sampled_at = serializers.DateTimeField(allow_null=True)
    cpu_percent = serializers.FloatField(allow_null=True)
    load_1m = serializers.FloatField(allow_null=True)
    mem_used_mb = serializers.IntegerField(allow_null=True)
    mem_total_mb = serializers.IntegerField(allow_null=True)
    disk_used_gb = serializers.FloatField(allow_null=True)
    disk_total_gb = serializers.FloatField(allow_null=True)
    net_rx_kbps = serializers.FloatField(allow_null=True)
    net_tx_kbps = serializers.FloatField(allow_null=True)
    cpu_series = serializers.ListField(child=serializers.FloatField())


class StatusOverviewSerializer(serializers.Serializer[dict[str, Any]]):
    """The internal status tier — app meta + the ordered pipeline-flow stages + catalog
    cardinality + latest valuation + recent run history. A computed aggregate (no model),
    so a plain Serializer; every nullable field sets ``allow_null=True`` by hand (the
    schema nullability gate only walks ModelSerializers — the ``MoverRowSerializer``
    rule, ``apps/valuation/serializers.py``)."""

    app = AppMetaSerializer()
    pipeline = PipelineStageSerializer(many=True)
    catalog = CatalogSerializer()
    valuation = ValuationSummarySerializer()
    recent_runs = RecentRunSerializer(many=True)
