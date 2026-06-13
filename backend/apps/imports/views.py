from __future__ import annotations

from typing import Any

from django.db.models import Count, Q, QuerySet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import parsers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer

from apps.imports.models import ImportBatch, ImportRow, MatchConfidence, RowStatus
from apps.imports.serializers import (
    ImportBatchSerializer,
    ImportRowOverrideSerializer,
    ImportRowSerializer,
    ImportUploadSerializer,
)
from apps.imports.sync import (
    ImportRowNotActionable,
    approve_row,
    override_row,
    reject_row,
    run_import,
)

# Cap the synchronous upload's content size (slice 6, DECISIONS 2026-05-29). This bounds the
# inline import's PARSE/PROCESS time and rejects an oversized file after receipt -- it does NOT
# bound receive-side memory: the parser has already buffered the whole body by the time create()
# runs, so a true receive guard would be a front-proxy body limit. A full personal DS export is a
# few thousand rows / well under a MB, so 10 MB is generous headroom. The file part is exempt from
# DATA_UPLOAD_MAX_MEMORY_SIZE (that governs non-file form fields). NOTE: this cap must stay BELOW
# the frontend's Next proxy buffer (experimental.proxyClientMaxBodySize, currently 20 MB in
# next.config.ts) so the proxy never silently truncates a file this endpoint would accept -- the
# proxy forwards an over-buffer body as a truncated prefix with no error (Codex review 2026-05-30).
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Bound the synchronous import by ROW COUNT, not just bytes (Codex review 2026-05-30). The byte
# cap doesn't bound the per-row DB work / request duration, and run_import runs inline in the
# request, so a pathologically large export could outlast a worker/proxy timeout and leave a
# stuck PROCESSING batch. Sized to the request timeout it must fit, MEASURED (adversarial
# review 2026-06-12): a worst-case all-materialize import runs ~4.4ms/row against local
# Postgres (50k rows = 218s, far past gunicorn's --timeout 120 in the prod image), so 10k
# ≈ 44s local / est. ≤100s over Railway private networking — inside 120s with margin. A real
# personal collection is a few thousand rows, so 10k still clears any plausible export 3-5x
# over; a larger collection splits across files safely (per-holding re-import dedup ratchets).
# Enforced at the HTTP boundary only -- the management command imports a trusted local file and
# is uncapped. Counted via newlines (a cheap upper bound: lines >= data rows).
MAX_UPLOAD_ROWS = 10_000

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
    create=extend_schema(
        request=ImportUploadSerializer,
        responses={201: ImportBatchSerializer, 400: OpenApiTypes.OBJECT},
        summary="Upload a Dragon Shield CSV → run the import",
    ),
)
class ImportBatchViewSet(viewsets.ReadOnlyModelViewSet[ImportBatch]):
    """Import history + upload. List/retrieve batches with derived per-status row counts, and
    POST a Dragon Shield CSV to ``/api/imports/batches/`` to run an import (slice 6). The review
    surface acts on a batch's *rows* (``ImportRowViewSet``), never on batches directly. Defining
    ``create`` makes the router bind POST on the collection route (no separate mixin needed)."""

    serializer_class = ImportBatchSerializer
    # Upload is multipart; list/retrieve are GET (no body), so a viewset-wide MultiPartParser is
    # harmless for them and lets drf-spectacular emit multipart/form-data for create.
    parser_classes = [parsers.MultiPartParser]

    def get_queryset(self) -> QuerySet[ImportBatch]:
        # Explicit order_by, not just Meta.ordering: the count annotations add a GROUP BY, which
        # flips QuerySet.ordered to False (Meta ordering still applies in SQL, but the paginator
        # only trusts an explicit order_by) — without it DRF emits UnorderedObjectListWarning.
        return _with_row_counts(ImportBatch.objects.all()).order_by("-created_at", "-id")

    def get_serializer_class(self) -> type[BaseSerializer[Any]]:
        if self.action == "create":
            return ImportUploadSerializer
        return ImportBatchSerializer

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Upload a Dragon Shield CSV and run the import synchronously (DECISIONS 2026-05-29).

        Decode the file as utf-8-sig (mirroring the ``import_dragon_shield`` command — Excel
        "CSV UTF-8" saves prepend a BOM), hand the text to ``run_import``, and return the created
        batch with its derived row counts. A file that isn't a recognized DS export is **not** a
        request error: ``run_import`` records a FAILED ``ImportBatch`` (a durable history row), so
        this returns **201 with status=failed** and the frontend branches on ``batch.status``.
        Only a missing / oversized / non-text file is a **400**. Synchronous (not Celery) is fine
        at single-user few-thousand-row scale; ``MAX_UPLOAD_BYTES`` bounds the request time, and
        ``run_import`` returns a JSON-native result so a future Celery promotion is a drop-in."""
        input_serializer = ImportUploadSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        upload = input_serializer.validated_data["file"]
        if upload.size > MAX_UPLOAD_BYTES:
            mb = MAX_UPLOAD_BYTES // (1024 * 1024)
            raise ValidationError({"file": [f"file exceeds the {mb} MB upload limit"]})
        try:
            content = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError({"file": ["file must be UTF-8-encoded text"]}) from exc
        # Reject by row count before the inline import runs, so a pathologically large file can't
        # outlast the request worker mid-import. Counted with splitlines() — the SAME line
        # semantics the parser uses (dragon_shield.parse_dragon_shield splitlines() then
        # csv.DictReader) — NOT a bare "\n" count: a CR-delimited file has zero newlines but
        # still parses into all its rows, which would bypass the cap entirely (Codex review
        # 2026-06-12). The transient list is bounded by MAX_UPLOAD_BYTES and is exactly what
        # the parser would allocate for an accepted file anyway.
        if len(content.splitlines()) > MAX_UPLOAD_ROWS:
            raise ValidationError(
                {"file": [f"file has too many rows (limit {MAX_UPLOAD_ROWS:,})"]}
            )
        # original_filename is CharField(max_length=255); a crafted multipart filename can
        # exceed that and (on Postgres) would raise DataError inside run_import's first write,
        # BEFORE the FAILED-batch audit row is created -- an unhandled 500 that breaks the
        # "durable batch record" contract. Truncate at the import boundary. (sqlite silently
        # ignores the column bound, so we truncate in app code rather than rely on the DB.)
        filename = upload.name[:255]
        result = run_import(content, filename=filename)
        # The serializer reads the annotated count attributes, so re-fetch through the same
        # annotation rather than serializing the bare run_import batch instance.
        batch = _with_row_counts(ImportBatch.objects.filter(pk=result.batch_id)).get()
        return Response(ImportBatchSerializer(batch).data, status=status.HTTP_201_CREATED)


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
