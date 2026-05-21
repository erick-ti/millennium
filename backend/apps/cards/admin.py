from __future__ import annotations

from django.contrib import admin

from apps.cards.models import Card


@admin.register(Card)
class CardAdmin(admin.ModelAdmin[Card]):
    list_display = ["name", "passcode", "normalized_name", "updated_at"]
    search_fields = ["name", "normalized_name", "passcode"]
    ordering = ["name"]
    readonly_fields = ["created_at", "updated_at"]
