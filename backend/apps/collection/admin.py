from __future__ import annotations

from django.contrib import admin

from apps.collection.models import CollectionItem, StorageLocation


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
