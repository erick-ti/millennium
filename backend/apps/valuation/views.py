from __future__ import annotations

from typing import Any

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError

from apps.valuation.movers import (
    DEFAULT_ORDERING,
    DEFAULT_WINDOW_DAYS,
    ORDERING_CHOICES,
    WINDOW_DAYS_CHOICES,
    MoverRow,
    compute_collection_movers,
    order_rows,
)
from apps.valuation.serializers import MoverRowSerializer


@extend_schema_view(
    list=extend_schema(
        summary="Biggest price movers among owned (printing, edition) pairs",
        parameters=[
            OpenApiParameter(
                "window",
                OpenApiTypes.INT,
                enum=list(WINDOW_DAYS_CHOICES),
                description=(
                    "Lookback window in days; one of "
                    f"{list(WINDOW_DAYS_CHOICES)} (default {DEFAULT_WINDOW_DAYS})."
                ),
            ),
            OpenApiParameter(
                "ordering",
                OpenApiTypes.STR,
                enum=list(ORDERING_CHOICES),
                description=(
                    f"Sort key (default {DEFAULT_ORDERING!r}). A leading '-' is "
                    "descending; rows with a null percent (sub-floor base) always sort last."
                ),
            ),
        ],
    ),
)
class MoversViewSet(mixins.ListModelMixin, viewsets.GenericViewSet[Any]):
    """Read-only "biggest movers" analytics (DECISIONS 2026-05-31): each owned
    ``(printing, edition)``'s price change over a selectable window. Rows are
    computed from the valuation engine's usable-price helpers across two date
    anchors (today and today - window), scoped to currently-held holdings, then
    server-ordered (the ``?ordering=`` allowlist) and paginated — like every other
    list endpoint. Inherits the global ``IsAuthenticated`` + ``PageNumberPagination``."""

    serializer_class = MoverRowSerializer

    def _validated_window(self) -> int:
        raw = self.request.query_params.get("window")
        if raw is None:
            return DEFAULT_WINDOW_DAYS
        if not raw.isdigit() or int(raw) not in WINDOW_DAYS_CHOICES:
            raise ValidationError({"window": f"must be one of {list(WINDOW_DAYS_CHOICES)}"})
        return int(raw)

    def _validated_ordering(self) -> str:
        raw = self.request.query_params.get("ordering")
        if raw is None:
            return DEFAULT_ORDERING
        if raw not in ORDERING_CHOICES:
            raise ValidationError({"ordering": f"must be one of {list(ORDERING_CHOICES)}"})
        return raw

    # Not a real QuerySet: movers are computed cross-model from two price anchors,
    # reusing the engine's usable-price helpers (which return Python dicts —
    # re-deriving them in ORM annotations would reintroduce the high-only-masks-usable
    # bug, DECISIONS 2026-05-25). ListModelMixin paginates + serializes this list, and
    # drf-spectacular still emits the standard paginated envelope from serializer_class
    # + the pagination class. Params are validated here so an unknown window/ordering
    # 400s (the manual param-validation idiom; the list-only nature is implicit — this
    # viewset has only the list action).
    def get_queryset(self) -> list[MoverRow]:  # type: ignore[override]
        return order_rows(
            compute_collection_movers(window_days=self._validated_window()),
            self._validated_ordering(),
        )
