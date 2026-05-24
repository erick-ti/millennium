from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.pricing.models import ExternalPriceId, PriceSnapshot, UnmatchedProduct


@admin.register(ExternalPriceId)
class ExternalPriceIdAdmin(admin.ModelAdmin[ExternalPriceId]):
    list_display = ["provider", "external_id", "printing", "updated_at"]
    list_select_related = ["printing", "printing__card"]
    list_filter = ["provider"]
    search_fields = [
        "external_id",
        "printing__set_code",
        "printing__set_name",
        "printing__card__name",
    ]
    ordering = ["provider", "external_id"]
    readonly_fields = ["created_at", "updated_at"]
    autocomplete_fields = ["printing"]


@admin.register(PriceSnapshot)
class PriceSnapshotAdmin(admin.ModelAdmin[PriceSnapshot]):
    list_display = [
        "printing",
        "edition",
        "source",
        "snapshot_date",
        "market_price",
        "confidence",
    ]
    list_select_related = ["printing", "printing__card"]
    list_filter = ["source", "edition", "snapshot_date"]
    date_hierarchy = "snapshot_date"
    search_fields = [
        "printing__set_code",
        "printing__set_name",
        "printing__card__name",
    ]
    ordering = ["printing", "edition", "-snapshot_date", "source"]
    readonly_fields = ["created_at", "updated_at"]
    autocomplete_fields = ["printing"]

    def has_delete_permission(self, request: HttpRequest, obj: PriceSnapshot | None = None) -> bool:
        # Append-only price history is never hand-deleted from admin; returning
        # False also drops the bulk delete_selected action.
        return False

    def has_change_permission(self, request: HttpRequest, obj: PriceSnapshot | None = None) -> bool:
        # Append-only: editing an existing row is forbidden (a correction is a
        # recompute), so existing rows render view-only. The obj=None (model-level)
        # case still defers to Django's permission check — it gates the changelist —
        # so don't hard-code True there (that would leak history to unprivileged staff).
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)


@admin.register(UnmatchedProduct)
class UnmatchedProductAdmin(admin.ModelAdmin[UnmatchedProduct]):
    list_display = [
        "set_code",
        "set_rarity",
        "product_name",
        "reason",
        "status",
        "provider",
        "updated_at",
    ]
    list_filter = ["status", "reason", "provider"]
    # A work queue, not append-only history: triage status inline from the changelist.
    list_editable = ["status"]
    search_fields = ["external_id", "set_code", "set_rarity", "product_name", "set_name"]
    ordering = ["provider", "status", "set_code", "set_rarity"]
    readonly_fields = ["created_at", "updated_at"]
