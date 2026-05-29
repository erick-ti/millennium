from __future__ import annotations

from datetime import date

from django.db.models import QuerySet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from apps.portfolio.models import Portfolio, PortfolioValueSnapshot
from apps.portfolio.serializers import (
    PortfolioSerializer,
    PortfolioValueSnapshotSerializer,
)


def _parse_iso_date(value: str, *, field: str) -> date:
    """Parse an ISO-8601 date or raise a 400 for the named query param. Duplicated
    rather than shared because the apps deliberately don't import each other's helpers
    — the same trim/parse contract recurs across the read API (the pricing viewset
    has the same helper)."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError({field: "must be ISO-8601 date (YYYY-MM-DD)"}) from exc


@extend_schema_view(
    list=extend_schema(summary="List portfolios (with latest value snapshot inline)"),
    retrieve=extend_schema(summary="Retrieve one portfolio (with latest snapshot inline)"),
)
class PortfolioViewSet(viewsets.ReadOnlyModelViewSet[Portfolio]):
    """Read-only portfolios. Each row carries the latest ``PortfolioValueSnapshot``
    inline (NULL when a portfolio has never been valued, e.g. a freshly-created
    one from a Dragon Shield import that runs before the next 04:00 UTC
    valuation beat — DECISIONS 2026-05-25 slice 4c)."""

    serializer_class = PortfolioSerializer

    def get_queryset(self) -> QuerySet[Portfolio]:
        # Portfolio.name has a UNIQUE constraint (DECISIONS 2026-05-22) so name
        # alone is a fully deterministic order; id added defensively.
        return Portfolio.objects.all().order_by("name", "id")


@extend_schema_view(
    list=extend_schema(
        summary="List / filter portfolio value snapshots (append-only daily history)",
        parameters=[
            OpenApiParameter(
                "portfolio",
                OpenApiTypes.INT,
                description="Filter to one portfolio's snapshot timeline.",
            ),
            OpenApiParameter(
                "from",
                OpenApiTypes.DATE,
                description="Inclusive lower bound on ``snapshot_date``.",
            ),
            OpenApiParameter(
                "to",
                OpenApiTypes.DATE,
                description="Inclusive upper bound on ``snapshot_date``.",
            ),
        ],
    ),
    retrieve=extend_schema(summary="Retrieve one portfolio value snapshot"),
)
class PortfolioValueSnapshotViewSet(viewsets.ReadOnlyModelViewSet[PortfolioValueSnapshot]):
    """Read-only append-only daily valuation timeline. The slice-5 portfolio
    chart consumes ``?portfolio=&from=&to=`` to pull a range, then renders the
    value series. ``unrealized_gain`` may be NULL on a row (partial coverage —
    DECISIONS 2026-05-25 slice 4a); consumers handle NULL distinctly from 0."""

    serializer_class = PortfolioValueSnapshotSerializer

    def get_queryset(self) -> QuerySet[PortfolioValueSnapshot]:
        # (portfolio, snapshot_date) is unique; -snapshot_date is the natural
        # latest-first order. id tiebreaker for cross-portfolio listings.
        qs = PortfolioValueSnapshot.objects.select_related("portfolio").order_by(
            "portfolio_id", "-snapshot_date", "id"
        )
        if self.action != "list":
            return qs

        params = self.request.query_params
        portfolio = params.get("portfolio")
        if portfolio is not None:
            if not portfolio.isdigit():
                raise ValidationError({"portfolio": "must be an integer portfolio id"})
            qs = qs.filter(portfolio_id=int(portfolio))

        date_from = params.get("from")
        if date_from is not None:
            qs = qs.filter(snapshot_date__gte=_parse_iso_date(date_from, field="from"))

        date_to = params.get("to")
        if date_to is not None:
            qs = qs.filter(snapshot_date__lte=_parse_iso_date(date_to, field="to"))

        return qs
