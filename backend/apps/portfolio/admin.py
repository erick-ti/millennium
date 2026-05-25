from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.portfolio.models import Portfolio, PortfolioValueSnapshot


@admin.register(Portfolio)
class PortfolioAdmin(admin.ModelAdmin[Portfolio]):
    list_display = ["name", "created_at", "updated_at"]
    # search_fields so collection_items can target this via autocomplete_fields
    # (autocomplete requires the referenced admin to define search_fields).
    search_fields = ["name"]
    ordering = ["name"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(PortfolioValueSnapshot)
class PortfolioValueSnapshotAdmin(admin.ModelAdmin[PortfolioValueSnapshot]):
    list_display = [
        "portfolio",
        "snapshot_date",
        "market_value",
        "cost_basis",
        "unrealized_gain",
        "coverage_complete",
        "valuation_version",
    ]
    list_select_related = ["portfolio"]
    list_filter = ["snapshot_date", "valuation_method"]
    date_hierarchy = "snapshot_date"
    search_fields = ["portfolio__name"]
    autocomplete_fields = ["portfolio"]
    ordering = ["portfolio", "-snapshot_date"]
    readonly_fields = ["created_at", "updated_at"]

    def has_delete_permission(
        self, request: HttpRequest, obj: PortfolioValueSnapshot | None = None
    ) -> bool:
        # Append-only valuation history is never hand-deleted from admin (a
        # correction is a recompute, not a delete). Returning False also drops the
        # bulk delete_selected action, which Django gates on delete permission.
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: PortfolioValueSnapshot | None = None
    ) -> bool:
        # Append-only: editing an already-written snapshot is forbidden (a
        # correction is a recompute, not an edit), so existing rows render
        # view-only. The obj=None (model-level) case must still defer to Django's
        # permission check — it gates the changelist via has_view_or_change_permission
        # — so hard-coding True there would leak the history to unprivileged staff.
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)

    @admin.display(boolean=True, description="Complete")
    def coverage_complete(self, obj: PortfolioValueSnapshot) -> bool:
        """At-a-glance: did this valuation cover the whole portfolio (both priced
        and costed)? A partial snapshot has unrealized_gain NULL."""
        return obj.is_complete
