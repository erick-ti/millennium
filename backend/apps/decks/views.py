from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import Count, QuerySet, Sum
from django.db.models.functions import Coalesce
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from apps.decks.models import Deck, DeckMembership
from apps.decks.serializers import DeckMembershipSerializer, DeckSerializer


@extend_schema_view(
    list=extend_schema(summary="List decks (each with its member count)"),
    retrieve=extend_schema(summary="Retrieve a deck"),
    create=extend_schema(
        responses={201: DeckSerializer, 400: OpenApiTypes.OBJECT},
        summary="Create a deck",
    ),
    update=extend_schema(summary="Replace a deck"),
    partial_update=extend_schema(summary="Rename / edit a deck"),
    destroy=extend_schema(
        summary="Delete a deck (its memberships cascade away)",
    ),
)
class DeckViewSet(viewsets.ModelViewSet[Deck]):
    """Full CRUD for decks — mutable user resources (the ``Portfolio`` posture; the
    explicit-full-surface case where ``ModelViewSet`` is the honest choice). List +
    retrieve carry a ``member_count`` annotation; create / rename (PATCH) / delete are the
    inherited writes (global session auth + ``proxy.ts`` CSRF apply). Members are managed
    through the separate ``DeckMembershipViewSet`` (a deck's member feed + add/remove), NOT
    nested here — members are mutable + paginated, so they live on their own flat endpoint
    (the import-batch-detail header/rows split, not the cards/collection nested-detail
    shape). Inherits ``IsAuthenticated`` + ``PageNumberPagination``.
    """

    serializer_class = DeckSerializer

    def get_queryset(self) -> QuerySet[Deck]:
        # Annotate member_count for BOTH list and retrieve — the serializer field reads it
        # (a create/update response falls back to a live count). name isn't unique, so id
        # is the deterministic paginator tiebreaker; the Count's GROUP BY also flips the
        # queryset to unordered, which makes the explicit order_by REQUIRED (filterwarnings
        # promotes UnorderedObjectListWarning to a failure), not merely good practice.
        return Deck.objects.annotate(member_count=Count("memberships")).order_by(
            "name", "id"
        )


@extend_schema_view(
    list=extend_schema(
        summary="List deck memberships (filter to one deck with ?deck=)",
        parameters=[
            OpenApiParameter(
                "deck",
                OpenApiTypes.INT,
                description="Filter memberships to one deck.",
            ),
        ],
    ),
    create=extend_schema(
        request=DeckMembershipSerializer,
        responses={
            201: DeckMembershipSerializer,
            400: OpenApiTypes.OBJECT,
            409: OpenApiTypes.OBJECT,
        },
        summary="Add an owned holding to a deck",
    ),
    destroy=extend_schema(summary="Remove a holding from a deck"),
)
class DeckMembershipViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet[DeckMembership],
):
    """A deck's membership feed + add/remove. A membership is a stateless join row, so this
    is a plain List+Create+Destroy resource (NOT the imports ``@action``-chokepoint style,
    which exists only for that app's batch/row state machine — decks have no such state).
    OWNED-only is structural: the membership FKs ``CollectionItem``, so a non-owned card
    has no id to add. Add is idempotent-aware — a duplicate ``(deck, collection_item)``
    returns 409 (the holding is already in the deck), never a second row. Inherits
    ``IsAuthenticated`` + ``PageNumberPagination``.
    """

    serializer_class = DeckMembershipSerializer

    def get_queryset(self) -> QuerySet[DeckMembership]:
        qs = (
            DeckMembership.objects.select_related(
                "collection_item",
                "collection_item__printing",
                "collection_item__printing__card",
                "collection_item__portfolio",
            )
            # The holding's copy count — SUM over its lots (the CollectionItem.quantity
            # definition, DECISIONS 2026-05-18), Coalesce'd to 0 for a lot-less holding. A
            # deck counts distinct HOLDINGS (member_count); this per-row quantity is how the
            # member table shows that one holding is N copies (Codex adversarial review
            # 2026-05-31). The Sum's GROUP BY makes the explicit order_by required.
            .annotate(quantity=Coalesce(Sum("collection_item__lots__quantity"), 0))
            .order_by("deck", "id")
        )
        # List-only filter guard: a stray ?deck= on a destroy would route through
        # filter_queryset and 404 the lookup (the imports lesson). Filters apply to list.
        if self.action != "list":
            return qs
        deck = self.request.query_params.get("deck")
        if deck is not None:
            if not deck.isdigit():
                raise ValidationError({"deck": "must be an integer deck id"})
            qs = qs.filter(deck_id=int(deck))
        return qs

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        # Idempotent-aware add: validate the FKs, then get_or_create on the
        # (deck, collection_item) UNIQUE so a concurrent double-add can't create two rows.
        # A holding already in the deck → a clean 409 (informative; the import 409 the
        # frontend already reads), never a silent duplicate or an IntegrityError 500.
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deck = serializer.validated_data["deck"]
        collection_item = serializer.validated_data["collection_item"]
        with transaction.atomic():
            membership, created = DeckMembership.objects.get_or_create(
                deck=deck, collection_item=collection_item
            )
        if not created:
            return Response(
                {"detail": "This holding is already in the deck."},
                status=status.HTTP_409_CONFLICT,
            )
        # Re-fetch through the annotated queryset so the 201 carries `quantity` — the
        # get_or_create'd instance has no annotation (the member_count create-trap, here too).
        membership = self.get_queryset().get(pk=membership.pk)
        out = self.get_serializer(membership)
        headers = self.get_success_headers(out.data)
        return Response(out.data, status=status.HTTP_201_CREATED, headers=headers)
