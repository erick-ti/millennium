from __future__ import annotations

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.portfolio.models import Portfolio, PortfolioValueSnapshot


class PortfolioValueSnapshotSerializer(serializers.ModelSerializer[PortfolioValueSnapshot]):
    """Append-only daily valuation row.

    ``unrealized_gain`` is nullable: NULL means partial coverage, and the difference
    of two different subsets isn't a gain. The three
    coverage counts say how much of the portfolio each total covers; the derived
    ``*_complete`` / ``is_complete`` flags read off the model properties (so they
    can't drift from the counts). Consumers must handle NULL ``unrealized_gain``.
    """

    market_value_complete = serializers.BooleanField(read_only=True)
    cost_basis_complete = serializers.BooleanField(read_only=True)
    is_complete = serializers.BooleanField(read_only=True)

    class Meta:
        model = PortfolioValueSnapshot
        fields = [
            "id",
            "portfolio",
            "snapshot_date",
            "market_value",
            "liquidation_value",
            "cost_basis",
            "unrealized_gain",
            "total_card_count",
            "priced_card_count",
            "costed_card_count",
            "market_value_complete",
            "cost_basis_complete",
            "is_complete",
            "valuation_method",
            "valuation_version",
            "created_at",
        ]


class PortfolioSerializer(serializers.ModelSerializer[Portfolio]):
    """A portfolio + its latest ``PortfolioValueSnapshot`` inline (NULL when a
    portfolio has never been valued). The portfolio summary shows
    today's value at a glance without a second round-trip; the value-history
    chart consumes ``/api/portfolio/snapshots/?portfolio=&from=&to=``.

    N+1 risk on list is bounded by portfolio count (single digits in practice,
    e.g. Yubel Deck, Long-term hold). If portfolios ever scale, switch this
    to a correlated subquery / prefetched-singleton.
    """

    latest_snapshot = serializers.SerializerMethodField()

    class Meta:
        model = Portfolio
        fields = ["id", "name", "latest_snapshot"]

    # Pass an INSTANCE with allow_null=True (not the class) so drf-spectacular
    # marks the schema field nullable. Without this, the SerializerMethodField's
    # documented `None` return on never-valued portfolios is hidden from the
    # OpenAPI contract and the generated TS client types it as non-null, so a
    # UI dereferencing `portfolio.latest_snapshot.market_value` crashes
    # at runtime against a first-import-before-04:00-beat portfolio.
    @extend_schema_field(PortfolioValueSnapshotSerializer(allow_null=True))
    def get_latest_snapshot(self, obj: Portfolio) -> dict[str, Any] | None:
        # (portfolio, snapshot_date) is unique, so a single -snapshot_date order
        # is fully deterministic with no tiebreaker.
        snapshot = obj.value_snapshots.order_by("-snapshot_date").first()
        if snapshot is None:
            return None
        return PortfolioValueSnapshotSerializer(snapshot).data
