from __future__ import annotations

from django.contrib import admin

from apps.decks.models import Deck, DeckMembership


@admin.register(Deck)
class DeckAdmin(admin.ModelAdmin[Deck]):
    # Mutable user data — fully editable in admin (the StorageLocation / Portfolio /
    # AlertRule posture), NOT the append-only event/run lock.
    list_display = ["name", "created_at"]
    search_fields = ["name"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(DeckMembership)
class DeckMembershipAdmin(admin.ModelAdmin[DeckMembership]):
    list_display = ["deck", "collection_item", "created_at"]
    list_filter = ["deck"]
    # raw_id widget for the holding FK: there can be many CollectionItems, and this
    # avoids depending on a search_fields-equipped CollectionItem admin (autocomplete).
    raw_id_fields = ["collection_item"]
    readonly_fields = ["created_at", "updated_at"]
