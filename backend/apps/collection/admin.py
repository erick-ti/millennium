from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.collection.models import CollectionItem, CollectionLot, StorageLocation


@admin.register(StorageLocation)
class StorageLocationAdmin(admin.ModelAdmin[StorageLocation]):
    list_display = ["name", "created_at", "updated_at"]
    # search_fields so collection_items can target this via autocomplete_fields
    # (autocomplete requires the referenced admin to define search_fields).
    search_fields = ["name"]
    ordering = ["name"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(CollectionItem)
class CollectionItemAdmin(admin.ModelAdmin[CollectionItem]):
    list_display = [
        "printing",
        "edition",
        "condition",
        "language",
        "portfolio",
        "storage_location",
        "updated_at",
    ]
    list_select_related = ["printing", "printing__card", "portfolio", "storage_location"]
    list_filter = ["edition", "condition", "language"]
    search_fields = [
        "printing__set_code",
        "printing__set_name",
        "printing__card__name",
        "portfolio__name",
        "storage_location__name",
    ]
    autocomplete_fields = ["printing", "portfolio", "storage_location"]
    ordering = ["portfolio", "printing"]
    readonly_fields = ["created_at", "updated_at"]

    # Drop the one-click bulk "delete selected" action. A holding's child lots are
    # the only (non-re-derivable) cost-basis history, and the lot FK is CASCADE, so a
    # mass delete here would silently destroy that history. Single-object delete —
    # which shows the cascade-listing confirmation — stays available. Holding removal
    # will route through an explicit archive/dispose path in Phase 2 (DECISIONS 2026-05-22).
    def get_actions(self, request: HttpRequest) -> dict[str, Any]:
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


@admin.register(CollectionLot)
class CollectionLotAdmin(admin.ModelAdmin[CollectionLot]):
    list_display = ["collection_item", "quantity", "unit_cost", "acquired_at", "created_at"]
    list_select_related = [
        "collection_item",
        "collection_item__printing",
        "collection_item__portfolio",
    ]
    list_filter = ["acquired_at"]
    date_hierarchy = "acquired_at"
    search_fields = [
        "collection_item__printing__set_code",
        "collection_item__printing__card__name",
        "collection_item__portfolio__name",
    ]
    # autocomplete needs CollectionItemAdmin to define search_fields (it does).
    autocomplete_fields = ["collection_item"]
    ordering = ["collection_item", "acquired_at", "id"]
    readonly_fields = ["created_at", "updated_at"]

    # Same bulk-delete guard as CollectionItemAdmin — lots *are* the cost-basis history.
    def get_actions(self, request: HttpRequest) -> dict[str, Any]:
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions
