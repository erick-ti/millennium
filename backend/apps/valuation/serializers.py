from __future__ import annotations

from rest_framework import serializers

from apps.core.enums import Edition
from apps.valuation.movers import MoverRow


class MoverRowSerializer(serializers.Serializer[MoverRow]):
    """One owned ``(printing, edition)``'s price move over the window — a computed
    aggregate, not a model row, so a plain (hand-typed) ``Serializer``.

    ``pct_change`` is null when the older-anchor base is below the near-zero floor
    (the dollar move is still real and shown); ``variant_label`` is null for a
    no-variant printing. Both nullables set ``allow_null=True`` explicitly: the
    schema nullability gate (``test_schema.py``) only walks ``ModelSerializer``s, so
    a plain ``Serializer`` gets no automatic coverage. Money fields are
    ``DecimalField`` (serialized as strings, like ``PriceSnapshotSerializer``) so the
    frontend's ``parseDecimal`` keeps NULL distinct from 0; ``abs_change`` may be
    negative (a loss)."""

    printing = serializers.IntegerField(source="printing_id")
    card_id = serializers.IntegerField()
    card_name = serializers.CharField()
    set_code = serializers.CharField()
    set_rarity = serializers.CharField()
    variant_label = serializers.CharField(allow_null=True)
    edition = serializers.ChoiceField(choices=Edition.choices)
    start_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    end_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    abs_change = serializers.DecimalField(max_digits=12, decimal_places=2)
    pct_change = serializers.FloatField(allow_null=True)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
