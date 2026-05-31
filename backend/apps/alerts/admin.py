from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.alerts.models import AlertEvent, AlertRule, AlertRun


@admin.register(AlertRule)
class AlertRuleAdmin(admin.ModelAdmin[AlertRule]):
    # Mutable user config — fully editable in admin (the StorageLocation/Portfolio
    # posture), unlike the append-only event/run history below.
    list_display = ["name", "threshold_pct", "window_days", "direction", "is_active", "created_at"]
    list_filter = ["is_active", "direction", "window_days"]
    search_fields = ["name"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(AlertEvent)
class AlertEventAdmin(admin.ModelAdmin[AlertEvent]):
    list_display = [
        "rule_name",
        "printing",
        "edition",
        "pct_change",
        "dollar_change",
        "triggered_on",
    ]
    list_filter = ["edition", "rule_direction", "triggered_on"]
    date_hierarchy = "triggered_on"
    ordering = ["-triggered_on", "-id"]
    # Every field is machine-written by the daily evaluation; the whole row is view-only.
    readonly_fields = [
        "rule",
        "printing",
        "edition",
        "triggered_on",
        "rule_name",
        "rule_threshold_pct",
        "rule_window_days",
        "rule_direction",
        "start_price",
        "end_price",
        "pct_change",
        "dollar_change",
        "created_at",
        "updated_at",
    ]

    def has_delete_permission(
        self, request: HttpRequest, obj: AlertEvent | None = None
    ) -> bool:
        # Append-only event history is never hand-deleted from admin; returning False
        # also drops the bulk delete_selected action (the ValuationRunAdmin precedent).
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: AlertEvent | None = None
    ) -> bool:
        # Append-only: an existing event is immutable history, so existing rows render
        # view-only. The obj=None (model-level) case still defers to Django's permission
        # check — it gates the changelist — so don't hard-code True there (that would
        # leak history to unprivileged staff). The ValuationRunAdmin precedent.
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)


@admin.register(AlertRun)
class AlertRunAdmin(admin.ModelAdmin[AlertRun]):
    list_display = ["status", "rules_evaluated", "events_created", "created_at"]
    list_filter = ["status"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
    readonly_fields = [
        "status",
        "rules_evaluated",
        "events_created",
        "detail",
        "error",
        "created_at",
        "updated_at",
    ]

    def has_delete_permission(
        self, request: HttpRequest, obj: AlertRun | None = None
    ) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: AlertRun | None = None
    ) -> bool:
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)
