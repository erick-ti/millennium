from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.valuation.models import ValuationRun


@admin.register(ValuationRun)
class ValuationRunAdmin(admin.ModelAdmin[ValuationRun]):
    list_display = [
        "status",
        "portfolios_seen",
        "snapshots_created",
        "snapshots_existing",
        "holdings_valued",
        "holdings_unpriced",
        "created_at",
    ]
    list_filter = ["status"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
    # Every field is machine-written; the whole row is view-only in admin.
    readonly_fields = [
        "status",
        "portfolios_seen",
        "snapshots_created",
        "snapshots_existing",
        "holdings_valued",
        "holdings_unpriced",
        "detail",
        "error",
        "created_at",
        "updated_at",
    ]

    def has_delete_permission(
        self, request: HttpRequest, obj: ValuationRun | None = None
    ) -> bool:
        # Append-only run history is never hand-deleted from admin; returning False
        # also drops the bulk delete_selected action.
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: ValuationRun | None = None
    ) -> bool:
        # Append-only: an existing run is immutable history, so existing rows render
        # view-only. The obj=None (model-level) case still defers to Django's
        # permission check -- it gates the changelist -- so don't hard-code True there
        # (that would leak history to unprivileged staff). The SyncRunAdmin /
        # PriceSnapshotAdmin precedent (DECISIONS 2026-05-22 append-only-admin follow-up).
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)
