from __future__ import annotations

from rest_framework import serializers

from apps.cards.models import Card, CardPrinting


class CardPrintingSerializer(serializers.ModelSerializer[CardPrinting]):
    """A printing: this artwork at this rarity in this set.

    ``is_multi_variant`` flags an ambiguous-placeholder row whose generic
    ``(set_code, set_rarity)`` covers multiple sellable variants; the
    import-review UI surfaces it to let a reviewer weigh the MEDIUM downgrade
    the matcher applies to a match on it. ``card`` is the FK id; ``card_name``
    is denormalized read-only so a list response doesn't force a per-row
    `/api/cards/cards/{id}/` lookup.
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
    """Card identity in list shape. ``normalized_name`` is deliberately omitted:
    it's an internal lookup index, not API surface."""

    # Number of printings for this card, from the viewset's
    # ``Count("printings")`` annotation (not a stored field). The /cards
    # table renders it; a Count can't be NULL, so no ``allow_null``. Inherited by
    # CardDetailSerializer: the viewset annotates both list and retrieve.
    printings_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Card
        # ``archetype`` is an IMPLICIT ModelSerializer field over a nullable model
        # column, so ModelSerializer auto-derives ``allow_null=True``: the
        # class-level schema gate needs no manual annotation (unlike the explicit
        # nullable-field shapes). NULL is "no archetype", surfaced to the UI as such.
        fields = ["id", "passcode", "name", "archetype", "printings_count"]


class CardDetailSerializer(CardListSerializer):
    """Card detail nests its printings (most cards have at most a handful, so the
    nested payload stays small). The flat ``/api/cards/printings/?card={id}``
    endpoint remains available for filterable browsing."""

    printings = CardPrintingSerializer(many=True, read_only=True)

    class Meta(CardListSerializer.Meta):
        fields = [*CardListSerializer.Meta.fields, "printings"]
