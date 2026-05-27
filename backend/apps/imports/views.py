from __future__ import annotations

from django.db.models import Count, Q, QuerySet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from apps.imports.models import ImportBatch, ImportRow, MatchConfidence, RowStatus
from apps.imports.serializers import (
    ImportBatchSerializer,
    ImportRowOverrideSerializer,
    ImportRowSerializer,
)
from apps.imports.sync import (
    ImportRowNotActionable,
    approve_row,
    override_row,
    reject_row,
)

# The "needs a human" set is exactly the still-PENDING rows (== ImportRow.needs_review): a
# pending row is one the auto-path couldn't finish — a sub-EXACT match, a freshness-gated EXACT
# row, OR a changed-duplicate cost conflict (the last two are PENDING *with* EXACT confidence, so
# a `PENDING && != EXACT` rule would silently hide the conflict — see ImportRow.needs_review,
# DECISIONS 2026-05-27 round 2). Defined as the row-level Q here, mirrored relation-prefixed in
# the count below; both express the same model-level rule.
_NEEDS_REVIEW = Q(status=RowStatus.PENDING)


def _with_row_counts(batches: QuerySet[ImportBatch]) -> QuerySet[ImportBatch]:
    """Annotate each batch with its derived per-status row counts (the model does not
    denormalize them — DECISIONS 2026-05-25 slice 1). All counts are over the single ``rows``
    reverse relation, so one LEFT JOIN, no aggregate multiplication. ``rows_needs_review`` equals
    ``rows_pending`` (a task-oriented alias for the review UI)."""
    return batches.annotate(
        rows_total=Count("rows"),
        rows_materialized=Count("rows", filter=Q(rows__status=RowStatus.MATERIALIZED)),
        rows_skipped=Count("rows", filter=Q(rows__status=RowStatus.SKIPPED)),
        rows_pending=Count("rows", filter=Q(rows__status=RowStatus.PENDING)),
        rows_error=Count("rows", filter=Q(rows__status=RowStatus.ERROR)),
        rows_needs_review=Count("rows", filter=Q(rows__status=RowStatus.PENDING)),
    )


@extend_schema_view(
    list=extend_schema(summary="List import batches with per-status row counts"),
    retrieve=extend_schema(summary="Retrieve one import batch"),
)
class ImportBatchViewSet(viewsets.ReadOnlyModelViewSet[ImportBatch]):
    """Import history: list/retrieve batches with derived per-status row counts. Read-only —
    batches are created by ``run_import`` (the ``import_dragon_shield`` command); the review
    surface acts on a batch's *rows* (``ImportRowViewSet``), never on batches directly."""

    serializer_class = ImportBatchSerializer

    def get_queryset(self) -> QuerySet[ImportBatch]:
        # Explicit order_by, not just Meta.ordering: the count annotations add a GROUP BY, which
        # flips QuerySet.ordered to False (Meta ordering still applies in SQL, but the paginator
        # only trusts an explicit order_by) — without it DRF emits UnorderedObjectListWarning.
        return _with_row_counts(ImportBatch.objects.all()).order_by("-created_at", "-id")


@extend_schema_view(
    list=extend_schema(
        summary="List / filter import rows",
        parameters=[
            OpenApiParameter("batch", OpenApiTypes.INT, description="Filter to one batch's rows."),
            OpenApiParameter(
                "status",
                OpenApiTypes.STR,
                enum=RowStatus.values,
                description="Filter by pipeline status.",
            ),
            OpenApiParameter(
                "match_confidence",
                OpenApiTypes.STR,
                enum=MatchConfidence.values,
                description="Filter by match-quality tier.",
            ),
            OpenApiParameter(
                "needs_review",
                OpenApiTypes.BOOL,
                description="True = PENDING rows whose match is not a clean EXACT (the review set).",
            ),
        ],
    ),
    retrieve=extend_schema(summary="Retrieve one import row"),
)
class ImportRowViewSet(viewsets.ReadOnlyModelViewSet[ImportRow]):
    """The review queue. List/filter staged rows (by ``batch`` / ``status`` /
    ``match_confidence`` / ``needs_review`` — served by the ``(batch, status)`` index) and
    resolve a PENDING one via three actions, all routed through ``apps.imports.sync`` so the
    collection writes go through the single ``_materialize`` chokepoint (DECISIONS 2026-05-26
    slice 4): ``approve`` materializes the row's matched printing, ``override`` re-points it at
    a human-chosen printing, ``reject`` skips it."""

    serializer_class = ImportRowSerializer

    def get_queryset(self) -> QuerySet[ImportRow]:
        qs = ImportRow.objects.select_related(
            "batch", "matched_printing", "matched_printing__card"
        )
        # Query-param filtering is a `list`-only concern. get_object() (used by retrieve + the
        # detail actions) also runs the queryset through filter_queryset, so applying these
        # there would let a stray ?status= 404 an approve/override/reject — guard on the action.
        if self.action != "list":
            return qs

        params = self.request.query_params
        batch = params.get("batch")
        if batch is not None:
            if not batch.isdigit():
                raise ValidationError({"batch": "must be an integer batch id"})
            qs = qs.filter(batch_id=int(batch))

        row_status = params.get("status")
        if row_status is not None:
            if row_status not in RowStatus.values:
                raise ValidationError({"status": f"must be one of {RowStatus.values}"})
            qs = qs.filter(status=row_status)

        confidence = params.get("match_confidence")
        if confidence is not None:
            if confidence not in MatchConfidence.values:
                raise ValidationError(
                    {"match_confidence": f"must be one of {MatchConfidence.values}"}
                )
            qs = qs.filter(match_confidence=confidence)

        needs_review = params.get("needs_review")
        if needs_review is not None:
            wants = needs_review.strip().lower() in ("1", "true", "yes")
            qs = qs.filter(_NEEDS_REVIEW) if wants else qs.exclude(_NEEDS_REVIEW)

        return qs

    @extend_schema(
        request=None,
        responses={
            200: ImportRowSerializer,
            400: OpenApiTypes.OBJECT,
            409: ImportRowSerializer,
        },
        summary="Approve a row → materialize its matched printing",
    )
    @action(detail=True, methods=["post"])
    def approve(self, request: Request, pk: str | None = None) -> Response:
        """Materialize the row through ``_materialize``, overriding the auto-materialization
        freshness gate (the reviewer is the human attention that gate proxies for — DECISIONS
        2026-05-27). 200 with the updated row on MATERIALIZED/SKIPPED; 409 when the holding was
        already imported with a different quantity/cost/date (the row stays PENDING — the API
        surfaces the conflict rather than overwriting cost basis); 400 if the row isn't an
        approvable PENDING row with a matched printing."""
        row = self.get_object()
        try:
            fresh, outcome = approve_row(row)
        except ImportRowNotActionable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        code = status.HTTP_409_CONFLICT if outcome == RowStatus.PENDING else status.HTTP_200_OK
        return Response(self.get_serializer(fresh).data, status=code)

    @extend_schema(
        request=ImportRowOverrideSerializer,
        responses={200: ImportRowSerializer, 400: OpenApiTypes.OBJECT},
        summary="Override a row's matched printing",
    )
    @action(detail=True, methods=["post"])
    def override(self, request: Request, pk: str | None = None) -> Response:
        """Re-point the row at a human-chosen ``CardPrinting`` (body: ``{"printing": <id>}``)
        and leave it PENDING so the corrected match can be approved next. Approvable
        immediately after (``approve`` keys on a present matched printing, not on confidence)."""
        row = self.get_object()
        input_serializer = ImportRowOverrideSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            fresh = override_row(row, input_serializer.validated_data["printing"])
        except ImportRowNotActionable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(fresh).data, status=status.HTTP_200_OK)

    @extend_schema(
        request=None,
        responses={200: ImportRowSerializer, 400: OpenApiTypes.OBJECT},
        summary="Reject a row → skipped",
    )
    @action(detail=True, methods=["post"])
    def reject(self, request: Request, pk: str | None = None) -> Response:
        """Mark a PENDING row SKIPPED (the reviewer declines the holding). 400 if the row is
        not PENDING (a MATERIALIZED row already wrote a holding; an ERROR row is terminal)."""
        row = self.get_object()
        try:
            fresh = reject_row(row)
        except ImportRowNotActionable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(fresh).data, status=status.HTTP_200_OK)
