from __future__ import annotations

from datetime import date

from django.db.models import QuerySet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core.enums import Edition
from apps.pricing.models import PriceSnapshot
from apps.pricing.serializers import PriceSnapshotSerializer


def _parse_iso_date(value: str, *, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError({field: "must be ISO-8601 date (YYYY-MM-DD)"}) from exc


@extend_schema_view(
    list=extend_schema(
        summary="List / filter price snapshots (append-only daily history)",
        parameters=[
            OpenApiParameter(
                "printing",
                OpenApiTypes.INT,
                description="Filter to one printing.",
            ),
            OpenApiParameter(
                "edition",
                OpenApiTypes.STR,
                enum=Edition.values,
                description="Filter by edition (a pricing dimension).",
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
    retrieve=extend_schema(summary="Retrieve one price snapshot"),
)
class PriceSnapshotViewSet(viewsets.ReadOnlyModelViewSet[PriceSnapshot]):
    """Read-only append-only price history. List filterable by
    ``?printing=&edition=&from=&to=``, the price-chart shape. The
    ``latest`` action returns the most-recent snapshot for one
    ``(printing, edition)``, the "today's price" lookup; this is structured as
    an action (not the latest-first ordered list's first row) so a consumer
    can't accidentally page into history when it only wants today."""

    serializer_class = PriceSnapshotSerializer

    def get_queryset(self) -> QuerySet[PriceSnapshot]:
        # (printing, edition, source, snapshot_date) is the natural key, so the
        # composite order is fully deterministic; id tiebreaker is defensive.
        qs = PriceSnapshot.objects.select_related("printing").order_by(
            "printing_id", "edition", "-snapshot_date", "source", "id"
        )
        if self.action != "list":
            return qs

        params = self.request.query_params
        printing = params.get("printing")
        if printing is not None:
            if not printing.isdigit():
                raise ValidationError({"printing": "must be an integer printing id"})
            qs = qs.filter(printing_id=int(printing))

        edition = params.get("edition")
        if edition is not None:
            if edition not in Edition.values:
                raise ValidationError({"edition": f"must be one of {Edition.values}"})
            qs = qs.filter(edition=edition)

        date_from = params.get("from")
        if date_from is not None:
            qs = qs.filter(snapshot_date__gte=_parse_iso_date(date_from, field="from"))

        date_to = params.get("to")
        if date_to is not None:
            qs = qs.filter(snapshot_date__lte=_parse_iso_date(date_to, field="to"))

        return qs

    @extend_schema(
        summary="Latest snapshot for one (printing, edition): the 'today's price' lookup",
        parameters=[
            OpenApiParameter(
                "printing",
                OpenApiTypes.INT,
                required=True,
                description="Printing id (required).",
            ),
            OpenApiParameter(
                "edition",
                OpenApiTypes.STR,
                required=True,
                enum=Edition.values,
                description="Edition (required).",
            ),
        ],
        responses={
            200: PriceSnapshotSerializer,
            400: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
        },
    )
    @action(detail=False, methods=["get"])
    def latest(self, request: Request) -> Response:
        """The most recent snapshot for one ``(printing, edition)``. 404 when no
        snapshot exists for the pair (a printing TCGCSV doesn't price, or one
        the daily reconcile hasn't run for yet)."""
        params = request.query_params
        printing = params.get("printing")
        edition = params.get("edition")
        # Both required, so surface the missing field rather than returning an
        # arbitrary "latest across everything" row.
        if printing is None or edition is None:
            raise ValidationError({"detail": "both 'printing' and 'edition' are required"})
        if not printing.isdigit():
            raise ValidationError({"printing": "must be an integer printing id"})
        if edition not in Edition.values:
            raise ValidationError({"edition": f"must be one of {Edition.values}"})

        snapshot = (
            PriceSnapshot.objects.filter(printing_id=int(printing), edition=edition)
            .order_by("-snapshot_date", "source", "-id")
            .first()
        )
        if snapshot is None:
            return Response(
                {"detail": "no snapshot for this printing+edition"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(self.get_serializer(snapshot).data)
