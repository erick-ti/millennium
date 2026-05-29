from __future__ import annotations

from rest_framework import serializers

from apps.collection.models import CollectionItem, CollectionLot, StorageLocation


class StorageLocationSerializer(serializers.ModelSerializer[StorageLocation]):
    class Meta:
        model = StorageLocation
        fields = ["id", "name"]


class CollectionLotSerializer(serializers.ModelSerializer[CollectionLot]):
    """An acquisition batch under a ``CollectionItem`` — quantity + cost + date.

    ``unit_cost`` / ``acquired_at`` are nullable ("unknown" — DECISIONS 2026-05-18):
    a consumer treats NULL distinctly from ``0.00`` / today, the fake-gains-prevention
    posture that runs all the way up through the slice-4a coverage representation
    (a lot with NULL ``unit_cost`` drops out of the portfolio's ``costed_card_count``).
    ``import_source_ref`` is the per-holding-per-source dedup key the importer writes
    (DECISIONS 2026-05-26 slice 4); manual lots have NULL.
    """

    class Meta:
        model = CollectionLot
        fields = [
            "id",
            "collection_item",
            "quantity",
            "unit_cost",
            "acquired_at",
            "import_source_ref",
            "created_at",
            "updated_at",
        ]


class CollectionItemListSerializer(serializers.ModelSerializer[CollectionItem]):
    """One holding — N copies of one printing in one condition/edition/language/
    portfolio (DECISIONS 2026-05-18). ``quantity`` is derived (SUM over child lots,
    not stored on the item — DECISIONS 2026-05-18), supplied by the viewset's
    queryset annotation; an item with no lots reads as 0. The printing's identity
    fields are denormalized read-only so the slice-3 collection table doesn't
    need a per-row printing lookup.
    """

    quantity = serializers.IntegerField(read_only=True)
    portfolio_name = serializers.CharField(source="portfolio.name", read_only=True)
    storage_location_name = serializers.CharField(
        source="storage_location.name", read_only=True, default=None, allow_null=True
    )
    card_name = serializers.CharField(source="printing.card.name", read_only=True)
    set_code = serializers.CharField(source="printing.set_code", read_only=True)
    set_rarity = serializers.CharField(source="printing.set_rarity", read_only=True)
    variant_label = serializers.CharField(
        source="printing.variant_label", read_only=True, default=None, allow_null=True
    )

    class Meta:
        model = CollectionItem
        fields = [
            "id",
            "portfolio",
            "portfolio_name",
            "printing",
            "card_name",
            "set_code",
            "set_rarity",
            "variant_label",
            "condition",
            "edition",
            "language",
            "storage_location",
            "storage_location_name",
            "quantity",
        ]


class CollectionItemDetailSerializer(CollectionItemListSerializer):
    """Item detail nests its lots — the per-acquisition cost-basis history slice
    4 (card detail) and slice 5 (portfolio drill-down) want together with the
    aggregate. Lots arrive in the model's natural order (acquired_at ascending,
    nulls last; id tiebreaker — DECISIONS 2026-05-22)."""

    lots = CollectionLotSerializer(many=True, read_only=True)

    class Meta(CollectionItemListSerializer.Meta):
        fields = [*CollectionItemListSerializer.Meta.fields, "lots"]
