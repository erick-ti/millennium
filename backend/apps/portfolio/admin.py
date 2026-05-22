from __future__ import annotations

from django.contrib import admin

from apps.portfolio.models import Portfolio


@admin.register(Portfolio)
class PortfolioAdmin(admin.ModelAdmin[Portfolio]):
    list_display = ["name", "created_at", "updated_at"]
    # search_fields so collection_items can target this via autocomplete_fields
    # (autocomplete requires the referenced admin to define search_fields).
    search_fields = ["name"]
    ordering = ["name"]
    readonly_fields = ["created_at", "updated_at"]
