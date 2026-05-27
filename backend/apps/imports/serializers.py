from __future__ import annotations

from rest_framework import serializers

from apps.cards.models import CardPrinting
from apps.imports.models import ImportBatch, ImportRow


class MatchedPrintingSerializer(serializers.ModelSerializer[CardPrinting]):
    """Read-only nested view of a row's ``matched_printing`` for the review UI. Surfaces
    ``is_multi_variant`` so a reviewer sees that the matcher downgraded a known multi-variant
    placeholder to MEDIUM (and can weigh it before approving — the auto-path's gate is the
    human here; DECISIONS 2026-05-27)."""

    card_name = serializers.CharField(source="card.name", read_only=True)

    class Meta:
        model = CardPrinting
        fields = ["id", "card_name", "set_code", "set_rarity", "variant_label", "is_multi_variant"]


class ImportBatchSerializer(serializers.ModelSerializer[ImportBatch]):
    """One import's history record + per-status row counts. The counts are *derived* — the
    model deliberately does not denormalize them (DECISIONS 2026-05-25 slice 1) — and are
    supplied by the viewset's queryset annotation, so a summary can't drift from its rows.
    ``rows_needs_review`` counts every still-PENDING row (== ``ImportRow.needs_review``): a
    pending row is, by construction, one the auto-path left for a human or a re-sync —
    match-uncertain, freshness-gated, or a changed-duplicate cost conflict alike."""

    rows_total = serializers.IntegerField(read_only=True)
    rows_materialized = serializers.IntegerField(read_only=True)
    rows_skipped = serializers.IntegerField(read_only=True)
    rows_pending = serializers.IntegerField(read_only=True)
    rows_error = serializers.IntegerField(read_only=True)
    rows_needs_review = serializers.IntegerField(read_only=True)

    class Meta:
        model = ImportBatch
        fields = [
            "id",
            "source_format",
            "status",
            "original_filename",
            "error",
            "created_at",
            "updated_at",
            "rows_total",
            "rows_materialized",
            "rows_skipped",
            "rows_pending",
            "rows_error",
            "rows_needs_review",
        ]
        # rows_needs_review counts every still-PENDING row (== ImportRow.needs_review), so it
        # equals rows_pending — kept as a task-oriented alias for the review UI. The earlier
        # "PENDING + sub-EXACT" definition hid changed-duplicate EXACT conflicts (round 2).


class ImportRowSerializer(serializers.ModelSerializer[ImportRow]):
    """One staged row for the review queue. ``matched_printing`` is nested read-only (a human
    re-points it via the ``override`` action, not by writing this field); ``needs_review`` reads
    the ``ImportRow.needs_review`` property (still PENDING → needs a human/re-sync), the one
    definition the count and ``?needs_review`` filter also use, so they can't drift."""

    matched_printing = MatchedPrintingSerializer(read_only=True)
    needs_review = serializers.BooleanField(read_only=True)

    class Meta:
        model = ImportRow
        fields = [
            "id",
            "batch",
            "row_number",
            "raw_data",
            "normalized_data",
            "matched_printing",
            "match_confidence",
            "status",
            "error_message",
            "needs_review",
            "created_at",
            "updated_at",
        ]


class ImportRowOverrideSerializer(serializers.Serializer[ImportRow]):
    """Input for the ``override`` action: the ``CardPrinting`` a reviewer chooses for a row.
    ``PrimaryKeyRelatedField`` validates the printing exists (an unknown id → 400)."""

    printing = serializers.PrimaryKeyRelatedField(queryset=CardPrinting.objects.all())
