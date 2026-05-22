from __future__ import annotations

from django.contrib import admin

from apps.cards.models import Card, CardPrinting


@admin.register(Card)
class CardAdmin(admin.ModelAdmin[Card]):
    list_display = ["name", "passcode", "normalized_name", "updated_at"]
    search_fields = ["name", "normalized_name", "passcode"]
    ordering = ["name"]
    readonly_fields = ["created_at", "updated_at", "normalized_name"]


@admin.register(CardPrinting)
class CardPrintingAdmin(admin.ModelAdmin[CardPrinting]):
    list_display = ["set_code", "set_rarity", "variant_label", "set_name", "card", "updated_at"]
    list_select_related = ["card"]
    list_filter = ["set_rarity"]
    search_fields = ["set_code", "set_rarity", "variant_label", "set_name", "card__name"]
    ordering = ["set_code", "set_rarity"]
    readonly_fields = ["created_at", "updated_at"]
    autocomplete_fields = ["card"]
