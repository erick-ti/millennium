from __future__ import annotations

from django.db.models import Count, QuerySet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer

from apps.cards.models import Card, CardPrinting
from apps.cards.serializers import (
    CardDetailSerializer,
    CardListSerializer,
    CardPrintingSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary="List / search cards",
        parameters=[
            OpenApiParameter(
                "search",
                OpenApiTypes.STR,
                description="Case-insensitive substring match on card name.",
            ),
            OpenApiParameter(
                "archetype",
                OpenApiTypes.STR,
                description='Exact-match filter by Yu-Gi-Oh archetype (e.g. "Blue-Eyes").',
            ),
        ],
    ),
    retrieve=extend_schema(summary="Retrieve one card (with printings inline)"),
)
class CardViewSet(viewsets.ReadOnlyModelViewSet[Card]):
    """Read-only catalog of card identities. List returns ``{id, passcode, name}``
    and is ``?search=``-filterable by name (the slice-6 import-review override picker
    finds a card by name → lists its printings); retrieve nests printings (a card has
    at most a handful — DECISIONS 2026-05-18) so slice 4's card-detail view loads in
    one round-trip."""

    def get_queryset(self) -> QuerySet[Card]:
        # Card.name isn't unique after normalization (DECISIONS 2026-05-18), so
        # the surrogate id is the stable tiebreaker for deterministic pagination.
        # printings_count (slice 4 /cards table) is annotated for BOTH actions:
        # CardDetailSerializer inherits the field from CardListSerializer, so a
        # retrieve must also carry the annotation or serialization would
        # AttributeError on the missing attribute. Count never yields NULL.
        qs = Card.objects.annotate(printings_count=Count("printings")).order_by("name", "id")
        if self.action == "retrieve":
            return qs.prefetch_related("printings")
        # Filtering is a list-only concern: get_object() runs the queryset through
        # filter_queryset too, so a stray ?search= on a retrieve would 404 it (the
        # slice-5 import lesson). An empty/whitespace search is a cleared box, not a
        # filter — ignore it rather than returning zero rows.
        if self.action != "list":
            return qs
        search = self.request.query_params.get("search")
        if search is not None and search.strip():
            qs = qs.filter(name__icontains=search.strip())
        # Exact-match archetype facet (Phase 5). An empty/whitespace value is a
        # cleared dropdown, not "match the empty archetype" — ignore it (the
        # search-box convention); NULL archetypes have no selectable value.
        archetype = self.request.query_params.get("archetype")
        if archetype is not None and archetype.strip():
            qs = qs.filter(archetype=archetype.strip())
        return qs

    def get_serializer_class(self) -> type[BaseSerializer[Card]]:
        return CardDetailSerializer if self.action == "retrieve" else CardListSerializer

    @extend_schema(
        summary="List distinct archetypes",
        description=(
            "Every distinct non-null archetype, sorted — the source for the "
            "/cards archetype filter dropdown. Not paginated (a few hundred at most)."
        ),
        responses={200: {"type": "array", "items": {"type": "string"}}},
    )
    @action(detail=False, methods=["get"], url_path="archetypes")
    def archetypes(self, request: Request) -> Response:
        # A flat sorted list of distinct archetypes for the filter dropdown. Reads
        # Card.objects directly (not get_queryset) to skip the printings_count
        # annotation + name ordering, which are irrelevant to a distinct-archetype
        # scan. NULLs excluded — "no archetype" isn't a filterable value.
        values = (
            Card.objects.exclude(archetype__isnull=True)
            .order_by("archetype")
            .values_list("archetype", flat=True)
            .distinct()
        )
        return Response(list(values))


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
