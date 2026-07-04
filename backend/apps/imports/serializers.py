from __future__ import annotations

from rest_framework import serializers

from apps.cards.models import CardPrinting
from apps.imports.models import ImportBatch, ImportRow


class MatchedPrintingSerializer(serializers.ModelSerializer[CardPrinting]):
    """Read-only nested view of a row's ``matched_printing`` for the review UI. Surfaces
    ``is_multi_variant`` so a reviewer sees that the matcher downgraded a known multi-variant
    placeholder to MEDIUM (and can weigh it before approving; the auto-path's gate is the
    human here)."""

    card_name = serializers.CharField(source="card.name", read_only=True)

    class Meta:
        model = CardPrinting
        fields = ["id", "card_name", "set_code", "set_rarity", "variant_label", "is_multi_variant"]


class ImportBatchSerializer(serializers.ModelSerializer[ImportBatch]):
    """One import's history record + per-status row counts. The counts are *derived*: the
    model deliberately does not denormalize them, and are
    supplied by the viewset's queryset annotation, so a summary can't drift from its rows.
    ``rows_needs_review`` counts every still-PENDING row (== ``ImportRow.needs_review``): a
    pending row is, by construction, one the auto-path left for a human or a re-sync,
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
        # equals rows_pending, kept as a task-oriented alias for the review UI. The earlier
        # "PENDING + sub-EXACT" definition hid changed-duplicate EXACT conflicts.


class ImportRowSerializer(serializers.ModelSerializer[ImportRow]):
    """One staged row for the review queue. ``matched_printing`` is nested read-only (a human
    re-points it via the ``override`` action, not by writing this field); ``needs_review`` reads
    the ``ImportRow.needs_review`` property (still PENDING → needs a human/re-sync), the one
    definition the count and ``?needs_review`` filter also use, so they can't drift.

    ``allow_null=True`` on ``matched_printing`` is required: the model
    FK is nullable and UNMATCHED rows ship ``matched_printing=None`` as a normal state, without
    it the OpenAPI schema declares the property non-null and the generated TS client crashes a
    review UI dereferencing ``row.matched_printing.card_name`` on an unmatched row (which is
    precisely the row the reviewer most needs to act on). Same bug class as
    ``Portfolio.latest_snapshot``, different shape: that one used
    ``@extend_schema_field(Class)``; this one is a direct nested serializer assignment. Both
    shapes need explicit nullability.
    """

    matched_printing = MatchedPrintingSerializer(read_only=True, allow_null=True)
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


class ImportUploadSerializer(serializers.Serializer[ImportBatch]):
    """Input for the batch upload (slice 6): a Dragon Shield CSV file. ``FileField`` makes a
    missing/empty file a clean 400, and (with ``MultiPartParser`` on the viewset) drives
    drf-spectacular to emit a ``multipart/form-data`` request body in the schema/TS client.
    The view decodes the file as utf-8-sig and hands the text to ``run_import``."""

    file = serializers.FileField(help_text="A Dragon Shield CSV export.")
