from __future__ import annotations

from django.db.models import QuerySet, Sum
from django.db.models.functions import Coalesce
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.serializers import BaseSerializer

from apps.collection.models import CollectionItem, CollectionLot
from apps.collection.serializers import (
    CollectionItemDetailSerializer,
    CollectionItemListSerializer,
    CollectionLotSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary="List / filter collection items (with aggregate quantity)",
        parameters=[
            OpenApiParameter(
                "portfolio",
                OpenApiTypes.INT,
                description="Filter to one portfolio's holdings.",
            ),
        ],
    ),
    retrieve=extend_schema(
        summary="Retrieve one holding (with lots inline)",
    ),
)
class CollectionItemViewSet(viewsets.ReadOnlyModelViewSet[CollectionItem]):
    """Read-only collection holdings. List returns one row per holding with
    ``quantity`` = SUM over lots (an item with no lots reads as 0); retrieve
    nests the lots — the per-acquisition cost-basis history. ``portfolio`` /
    ``printing`` FKs and the storage location are pre-joined for the list shape."""

    def get_queryset(self) -> QuerySet[CollectionItem]:
        qs = (
            CollectionItem.objects.select_related(
                "printing__card", "portfolio", "storage_location"
            )
            # quantity isn't a stored column (DECISIONS 2026-05-18 — cost basis
            # lives on lots, so the item's count is the SUM). Coalesce so an
            # item with zero lots reads as 0 instead of NULL.
            .annotate(quantity=Coalesce(Sum("lots__quantity"), 0))
            # The annotate's GROUP BY flips ordered=False, so an explicit
            # order_by is required for paginator determinism (slice-5 lesson).
            # (portfolio, printing) can alias when one portfolio holds the same
            # printing in different conditions; id is the tiebreaker.
            .order_by("portfolio", "printing", "id")
        )
        if self.action == "retrieve":
            qs = qs.prefetch_related("lots")
        if self.action != "list":
            return qs

        params = self.request.query_params
        portfolio = params.get("portfolio")
        if portfolio is not None:
            if not portfolio.isdigit():
                raise ValidationError({"portfolio": "must be an integer portfolio id"})
            qs = qs.filter(portfolio_id=int(portfolio))

        return qs

    def get_serializer_class(self) -> type[BaseSerializer[CollectionItem]]:
        return (
            CollectionItemDetailSerializer
            if self.action == "retrieve"
            else CollectionItemListSerializer
        )


@extend_schema_view(
    list=extend_schema(
        summary="List / filter collection lots",
        parameters=[
            OpenApiParameter(
                "item",
                OpenApiTypes.INT,
                description="Filter to one collection item's lots.",
            ),
            OpenApiParameter(
                "portfolio",
                OpenApiTypes.INT,
                description="Filter to one portfolio's lots (via the item join).",
            ),
        ],
    ),
    retrieve=extend_schema(summary="Retrieve one lot"),
)
class CollectionLotViewSet(viewsets.ReadOnlyModelViewSet[CollectionLot]):
    """Read-only per-acquisition lots. List filterable by ``?item=`` /
    ``?portfolio=`` (via the item join). Default order matches the model's
    natural (item, acquired_at-asc-nulls-last, id) — chronological within a
    holding, with unknown-date lots last."""

    serializer_class = CollectionLotSerializer

    def get_queryset(self) -> QuerySet[CollectionLot]:
        # Mirror Meta.ordering explicitly so a future annotation can't silently
        # drop pagination determinism (the imports lesson).
        from django.db.models import F

        qs = CollectionLot.objects.select_related(
            "collection_item__portfolio", "collection_item__printing"
        ).order_by(
            "collection_item",
            F("acquired_at").asc(nulls_last=True),
            "id",
        )
        if self.action != "list":
            return qs

        params = self.request.query_params
        item = params.get("item")
        if item is not None:
            if not item.isdigit():
                raise ValidationError({"item": "must be an integer item id"})
            qs = qs.filter(collection_item_id=int(item))

        portfolio = params.get("portfolio")
        if portfolio is not None:
            if not portfolio.isdigit():
                raise ValidationError({"portfolio": "must be an integer portfolio id"})
            qs = qs.filter(collection_item__portfolio_id=int(portfolio))

        return qs
