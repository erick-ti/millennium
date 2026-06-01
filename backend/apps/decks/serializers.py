from __future__ import annotations

from django.db.models import Sum
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.collection.models import CollectionItem
from apps.decks.models import Deck, DeckMembership


class DeckSerializer(serializers.ModelSerializer[Deck]):
    """A deck — read AND create/update (rename). With ``COMPONENT_SPLIT_REQUEST=True``
    one class serves read+write, so a POST/PATCH body carries only ``name``/``description``
    and the response echoes the saved deck.

    ``member_count`` is derived (the count of ``DeckMembership`` rows). The viewset
    annotates it on the list/retrieve queryset, but a freshly created/updated instance
    won't carry the annotation, so it is a ``SerializerMethodField`` that falls back to a
    live count — the response after a POST/PATCH must still serialize without an
    AttributeError (the cards ``printings_count`` annotation trap, made annotation-safe).
    """

    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Deck
        fields = [
            "id",
            "name",
            "description",
            "member_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    @extend_schema_field(OpenApiTypes.INT)
    def get_member_count(self, obj: Deck) -> int:
        annotated = getattr(obj, "member_count", None)
        if annotated is not None:
            return int(annotated)
        return obj.memberships.count()

    def validate_name(self, value: str) -> str:
        # Trim at the API boundary (the collection-import precedent) and reject a blank /
        # whitespace-only name with a clean 400. name isn't a natural key, so this is
        # hygiene, not dedup.
        trimmed = value.strip()
        if not trimmed:
            raise serializers.ValidationError("must not be blank")
        return trimmed


class DeckMembershipSerializer(serializers.ModelSerializer[DeckMembership]):
    """A deck membership — read AND create. The write side carries only ``deck`` +
    ``collection_item`` (both ``PrimaryKeyRelatedField`` → 400 on an unknown id);
    everything else is the owned holding's identity, denormalized read-only via
    ``source="collection_item.*"`` so the deck-detail member table renders without a
    per-row lookup (the ``CollectionItemListSerializer`` pattern). ``variant_label`` is
    nullable, and a ``source=``-pointed declared field is skipped by the schema
    nullability gate (it maps only 1:1 model fields), so it sets ``allow_null=True``
    explicitly (the ``AlertEventSerializer`` precedent).

    ``Meta.validators = []`` drops the auto ``UniqueTogetherValidator`` DRF would derive
    from the model's ``(deck, collection_item)`` UNIQUE: the viewset's ``create`` instead
    ``get_or_create``s and returns a clean 409 for an already-present holding (informative,
    and the same status the import-review frontend already reads), rather than a generic
    400 — while the DB UNIQUE still backstops a concurrent double-add.
    """

    collection_item = serializers.PrimaryKeyRelatedField(
        queryset=CollectionItem.objects.all()
    )
    # The holding's copy count (SUM of its lots), supplied by the viewset's queryset
    # annotation — so the member table shows that one tagged holding is N physical copies.
    # Non-null (Coalesce'd to 0), so no allow_null; not a DeckMembership model field, so the
    # class-level nullability gate skips it.
    quantity = serializers.IntegerField(read_only=True)
    card_name = serializers.CharField(
        source="collection_item.printing.card.name", read_only=True
    )
    set_code = serializers.CharField(
        source="collection_item.printing.set_code", read_only=True
    )
    set_rarity = serializers.CharField(
        source="collection_item.printing.set_rarity", read_only=True
    )
    variant_label = serializers.CharField(
        source="collection_item.printing.variant_label",
        read_only=True,
        allow_null=True,
    )
    condition = serializers.CharField(
        source="collection_item.condition", read_only=True
    )
    edition = serializers.CharField(source="collection_item.edition", read_only=True)
    language = serializers.CharField(
        source="collection_item.language", read_only=True
    )
    portfolio_name = serializers.CharField(
        source="collection_item.portfolio.name", read_only=True
    )

    class Meta:
        model = DeckMembership
        fields = [
            "id",
            "deck",
            "collection_item",
            "quantity",
            "card_name",
            "set_code",
            "set_rarity",
            "variant_label",
            "condition",
            "edition",
            "language",
            "portfolio_name",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]
        # Dedup is handled in DeckMembershipViewSet.create (409) so the auto unique-together
        # 400 doesn't pre-empt it; the DB UNIQUE remains the race-safe backstop.
        validators: list[object] = []

    def validate_collection_item(self, value: CollectionItem) -> CollectionItem:
        # A deck groups cards you actually HOLD — reject a zero-copy (lot-less) holding. Unlike
        # the collection *ledger* (which records depleted holdings, and filters quantity nowhere),
        # a deck is forward-looking ("what I'm playing"), so an owned-copies>0 check is the right
        # owned-only boundary here (Codex adversarial review 2026-05-31). Defense-in-depth: the
        # picker also filters these out, but a direct API call must still be rejected (clean 400).
        total = value.lots.aggregate(total=Sum("quantity"))["total"] or 0
        if total <= 0:
            raise serializers.ValidationError(
                "This holding has no copies (quantity 0) — add copies before tagging it into a deck."
            )
        return value
