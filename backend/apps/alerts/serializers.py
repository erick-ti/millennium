from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from apps.alerts.models import AlertEvent, AlertRule


class AlertRuleSerializer(serializers.ModelSerializer[AlertRule]):
    """A price-alert rule — read AND create. ``id``/timestamps are read-only, so a POST
    body carries only ``name``/``threshold_pct``/``window_days``/``direction``/``is_active``
    and the 201 echoes the saved rule (id + timestamps). With
    ``COMPONENT_SPLIT_REQUEST=True`` (SPECTACULAR_SETTINGS) drf-spectacular emits separate
    request/response schemas from this one class, so no second create-serializer is needed.

    ``window_days``/``direction`` are ChoiceFields auto-derived from the model's ``choices``
    (an off-menu value → 400). ``threshold_pct`` positivity is the model's DB CHECK; validate
    it here too so a non-positive value is a clean 400, not an IntegrityError 500."""

    class Meta:
        model = AlertRule
        fields = [
            "id",
            "name",
            "threshold_pct",
            "window_days",
            "direction",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_threshold_pct(self, value: Decimal) -> Decimal:
        if value <= 0:
            raise serializers.ValidationError("must be greater than 0")
        return value


class AlertEventSerializer(serializers.ModelSerializer[AlertEvent]):
    """One alert-feed row: the fired rule's fire-time snapshot + the moved printing's
    identity + the move. The ``rule_*`` columns are the immutable snapshot (so the feed
    renders faithfully even if the live rule was later edited); the printing identity is
    pulled via ``source="printing.*"`` (those declared fields are skipped by the schema
    nullability gate, which only maps 1:1 model fields, so ``variant_label`` sets
    ``allow_null=True`` explicitly). Money fields serialize as strings (DecimalField — the
    PriceSnapshot convention) so the frontend's ``parseDecimal`` keeps values exact."""

    card_id = serializers.IntegerField(source="printing.card_id", read_only=True)
    card_name = serializers.CharField(source="printing.card.name", read_only=True)
    set_code = serializers.CharField(source="printing.set_code", read_only=True)
    set_rarity = serializers.CharField(source="printing.set_rarity", read_only=True)
    variant_label = serializers.CharField(
        source="printing.variant_label", read_only=True, allow_null=True
    )

    class Meta:
        model = AlertEvent
        fields = [
            "id",
            "rule",
            "rule_name",
            "rule_threshold_pct",
            "rule_window_days",
            "rule_direction",
            "printing",
            "card_id",
            "card_name",
            "set_code",
            "set_rarity",
            "variant_label",
            "edition",
            "triggered_on",
            "start_price",
            "end_price",
            "pct_change",
            "dollar_change",
            "created_at",
        ]
