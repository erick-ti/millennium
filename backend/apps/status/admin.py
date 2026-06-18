from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.status.models import HostMetricSample


@admin.register(HostMetricSample)
class HostMetricSampleAdmin(admin.ModelAdmin[HostMetricSample]):
    """Read-only window onto the host telemetry (useful for "is the collector
    running?"). Samples are written by the management command and auto-pruned, never
    hand-edited, so add/change are disabled; delete stays available since these are
    disposable, not an auditable record."""

    list_display = (
        "created_at",
        "cpu_percent",
        "load_1m",
        "mem_used_mb",
        "disk_used_gb",
    )
    ordering = ("-created_at",)
    list_filter = ("created_at",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: HostMetricSample | None = None
    ) -> bool:
        # Defer the model-level (obj=None) case to super() so the changelist still
        # renders for a permitted user; only block editing an existing sample.
        if obj is None:
            return super().has_change_permission(request, obj)
        return False
