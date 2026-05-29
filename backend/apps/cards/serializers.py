from __future__ import annotations

from rest_framework import serializers

from apps.cards.models import Card, CardPrinting


class CardPrintingSerializer(serializers.ModelSerializer[CardPrinting]):
    """A printing — this artwork at this rarity in this set (DECISIONS 2026-05-18).

    ``is_multi_variant`` flags an ambiguous-placeholder row whose generic
    ``(set_code, set_rarity)`` covers multiple sellable variants (DECISIONS
    2026-05-24); slice 6's import-review UI surfaces it to let a reviewer weigh
    the MEDIUM downgrade the matcher applies to a match on it (DECISIONS
    2026-05-26). ``card`` is the FK id; ``card_name`` is denormalized read-only
    so a list response doesn't force a per-row `/api/cards/cards/{id}/` lookup.
    """

    card_name = serializers.CharField(source="card.name", read_only=True)

    class Meta:
        model = CardPrinting
        fields = [
            "id",
            "card",
            "card_name",
            "set_code",
            "set_rarity",
            "variant_label",
            "set_name",
            "is_multi_variant",
        ]


class CardListSerializer(serializers.ModelSerializer[Card]):
    """Card identity in list shape — ``normalized_name`` is deliberately omitted
    (it's an internal lookup index, not API surface — DECISIONS 2026-05-18)."""

    class Meta:
        model = Card
        fields = ["id", "passcode", "name"]


class CardDetailSerializer(CardListSerializer):
    """Card detail nests its printings (most cards have at most a handful, so the
    nested payload stays small). The flat ``/api/cards/printings/?card={id}``
    endpoint remains available for filterable browsing."""

    printings = CardPrintingSerializer(many=True, read_only=True)

    class Meta(CardListSerializer.Meta):
        fields = [*CardListSerializer.Meta.fields, "printings"]
