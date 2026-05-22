from __future__ import annotations

from django.contrib import admin

from apps.pricing.models import ExternalPriceId


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
