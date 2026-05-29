from __future__ import annotations

from rest_framework import serializers

from apps.pricing.models import PriceSnapshot


class PriceSnapshotSerializer(serializers.ModelSerializer[PriceSnapshot]):
    """One provider's price for a printing+edition on a given day (append-only —
    DECISIONS 2026-05-18). Every price point is nullable: a provider may report
    only some, so a consumer treats NULL distinctly from 0 (the same fake-zero
    avoidance pattern as ``CollectionLot.unit_cost`` and the slice-4a coverage
    fields). ``confidence`` is the multi-source scoring placeholder (1.0 today —
    one trusted source). ``source_subtype_name`` keeps the provider's raw subtype
    text (e.g. TCGCSV ``"1st Edition"``) for audit if the edition normalisation
    rule ever changes."""

    class Meta:
        model = PriceSnapshot
        fields = [
            "id",
            "printing",
            "edition",
            "source",
            "snapshot_date",
            "low_price",
            "mid_price",
            "high_price",
            "market_price",
            "direct_low_price",
            "confidence",
            "source_subtype_name",
            "created_at",
        ]
