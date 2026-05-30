from __future__ import annotations

from django.db.models import Count, QuerySet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.serializers import BaseSerializer

from apps.cards.models import Card, CardPrinting
from apps.cards.serializers import (
    CardDetailSerializer,
    CardListSerializer,
    CardPrintingSerializer,
)


@extend_schema_view(
    list=extend_schema(summary="List cards"),
    retrieve=extend_schema(summary="Retrieve one card (with printings inline)"),
)
class CardViewSet(viewsets.ReadOnlyModelViewSet[Card]):
    """Read-only catalog of card identities. List returns ``{id, passcode, name}``;
    retrieve nests printings (a card has at most a handful — DECISIONS 2026-05-18)
    so slice 4's card-detail view loads in one round-trip."""

    def get_queryset(self) -> QuerySet[Card]:
        # Card.name isn't unique after normalization (DECISIONS 2026-05-18), so
        # the surrogate id is the stable tiebreaker for deterministic pagination.
        # printings_count (slice 4 /cards table) is annotated for BOTH actions:
        # CardDetailSerializer inherits the field from CardListSerializer, so a
        # retrieve must also carry the annotation or serialization would
        # AttributeError on the missing attribute. Count never yields NULL.
        qs = Card.objects.annotate(printings_count=Count("printings")).order_by("name", "id")
        if self.action == "retrieve":
            qs = qs.prefetch_related("printings")
        return qs

    def get_serializer_class(self) -> type[BaseSerializer[Card]]:
        return CardDetailSerializer if self.action == "retrieve" else CardListSerializer


@extend_schema_view(
    list=extend_schema(
        summary="List / filter printings",
        parameters=[
            OpenApiParameter(
                "card",
                OpenApiTypes.INT,
                description="Filter to one card's printings.",
            ),
            OpenApiParameter(
                "set_code",
                OpenApiTypes.STR,
                description="Exact-match filter by set code.",
            ),
        ],
    ),
    retrieve=extend_schema(summary="Retrieve one printing"),
)
class CardPrintingViewSet(viewsets.ReadOnlyModelViewSet[CardPrinting]):
    """Read-only catalog of printings. List filterable by ``?card=`` / ``?set_code=``;
    global ``PageNumberPagination(PAGE_SIZE=100)`` paginates the ~14k-row catalog."""

    serializer_class = CardPrintingSerializer

    def get_queryset(self) -> QuerySet[CardPrinting]:
        # variant_label nullability and natural-key ambiguity (DECISIONS 2026-05-21)
        # mean (set_code, set_rarity) alone may alias for sibling variants; add id
        # as the deterministic tiebreaker for pagination.
        qs = CardPrinting.objects.select_related("card").order_by(
            "set_code", "set_rarity", "variant_label", "id"
        )
        # Query-param filtering is a list-only concern. get_object() also runs the
        # queryset through filter_queryset, so applying these to a detail action
        # would let a stray ?card= 404 a retrieve (the imports lesson, slice 5).
        if self.action != "list":
            return qs

        params = self.request.query_params
        card = params.get("card")
        if card is not None:
            if not card.isdigit():
                raise ValidationError({"card": "must be an integer card id"})
            qs = qs.filter(card_id=int(card))

        set_code = params.get("set_code")
        if set_code is not None:
            qs = qs.filter(set_code=set_code)

        return qs
