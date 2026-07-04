from __future__ import annotations

from typing import Any, cast

from django.conf import settings
from django.contrib.auth import get_user
from django.contrib.auth.models import User
from django.core.exceptions import RequestDataTooBig
from django.db.models import Count, Max, Min, Q, QuerySet
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import mixins, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet, ReadOnlyModelViewSet

from apps.audit import utils
from apps.audit.models import ActorType, AuditEvent, ErrorLog, ErrorSource
from apps.audit.serializers import (
    AuditEventSerializer,
    ClientErrorSerializer,
    ErrorGroupSerializer,
)
from apps.core.permissions import IsSuperUser, is_demo_user

# Hard cap on the beacon body, measured on the ACTUAL received bytes (len(request.body),
# already bounded by Django's DATA_UPLOAD_MAX_MEMORY_SIZE) rather than the client-supplied
# Content-Length header — which a hostile client can omit/understate to slip past a
# header-only check. The serializer also truncates each stored field; this just keeps the
# parse cheap on a public endpoint.
MAX_BEACON_BYTES = 16 * 1024


@method_decorator(csrf_protect, name="post")
class ClientErrorView(APIView):
    """Record a frontend error reported by the SPA (global handler / error boundary).

    ``AllowAny`` + no authenticators (the ``CsrfView`` precedent) — an anonymous visitor
    hits errors too, and the beacon must work before sign-in. ``csrf_protect`` re-arms
    CSRF on this POST (a bare ``APIView`` is ``csrf_exempt``, and ``SessionAuthentication``
    skips the check for anonymous requests, so it would otherwise be CSRF-naked); since
    only same-origin JS can read the ``csrftoken`` cookie, this also enforces same-origin.
    Its OWN throttle scope so beacon traffic can't drain the ``login`` bucket; the body is
    capped + every field truncated, and there is no free-form payload field, so the public
    surface can't be used to exfiltrate data or bloat the store.

    The actor/session are read from the SESSION (``get_user``) because
    ``authentication_classes=[]`` blanks DRF's ``request.user`` (the ``DemoLoginView``
    pattern). A PUBLIC beacon — anonymous OR the read-only demo, both publicly obtainable —
    is subject to a per-UTC-day quota (``MAX_PUBLIC_FRONTEND_ERRORS_PER_DAY``): the throttle
    bounds the rate but not the total, and CSRF is not a bot defense, so without the quota a
    scripted client (anonymous or holding a demo cookie) could fill the store before the
    prune window. Over quota → accept-but-DROP (still 204, no cap oracle). Only a real
    (non-demo) account is exempt. Returns 204 — the reporter is fire-and-forget."""

    permission_classes = [AllowAny]
    authentication_classes: list[type] = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "client_error"

    @extend_schema(
        summary="Report a frontend error",
        request=ClientErrorSerializer,
        responses={
            204: OpenApiResponse(description="Error recorded"),
            400: OpenApiResponse(description="Missing or blank message"),
            413: OpenApiResponse(description="Payload too large"),
            429: OpenApiResponse(description="Too many reports — retry later"),
        },
    )
    def post(self, request: Request) -> Response:
        # len(request.body) is the real received size (regardless of a lying/absent
        # Content-Length). A body over Django's DATA_UPLOAD_MAX_MEMORY_SIZE makes the read
        # itself raise RequestDataTooBig — catch it and return the SAME 413 rather than let
        # it propagate as a middleware-recorded backend 500 that would dodge the frontend
        # quota. Reading body first is safe — request.data then
        # parses from the cached body.
        try:
            oversized = len(request.body) > MAX_BEACON_BYTES
        except RequestDataTooBig:
            oversized = True
        if oversized:
            return Response(
                {"detail": "Payload too large."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        serializer = ClientErrorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = get_user(request._request)
        authed = bool(getattr(user, "is_authenticated", False))
        # The PUBLIC surface is anonymous OR the read-only demo — the demo session is
        # publicly obtainable (AllowAny demo-login), so it is NOT a trust boundary here.
        # Only a real (non-demo) account is exempt from the cap.
        is_public = (not authed) or is_demo_user(user)

        # Public daily quota — the storage bound the throttle (rate only) and CSRF (not a
        # bot defense) don't provide. Accept-but-drop over quota: a 204 either way so an
        # abuser gets no signal that the cap was hit. Soft cap (a small TOCTOU race under
        # concurrency is fine for an abuse bound). Real-account reports are never dropped.
        if is_public:
            since = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
            public_today = ErrorLog.objects.filter(
                source=ErrorSource.FRONTEND,
                actor_type__in=[ActorType.ANONYMOUS, ActorType.DEMO],
                created_at__gte=since,
            ).count()
            if public_today >= settings.MAX_PUBLIC_FRONTEND_ERRORS_PER_DAY:
                return Response(status=status.HTTP_204_NO_CONTENT)

        # window.location.pathname is expected, but strip any query string defensively so a
        # token passed as ?param never lands in the store.
        path = str(data["url"]).split("?", 1)[0]
        message = utils.truncate(str(data["message"]), utils.MAX_MESSAGE)
        name = str(data["name"])
        signature = message.splitlines()[0] if message else name

        ErrorLog.objects.create(
            source=ErrorSource.FRONTEND,
            actor=cast(User, user) if authed else None,
            actor_type=utils.classify_actor(user),
            actor_username=(getattr(user, "username", "") if authed else ""),
            session_key_hash=utils.hash_session_key(request._request.session.session_key),
            request_id=(str(data["request_id"]) or utils.current_request_id())[:64],
            fingerprint=utils.fingerprint_error(
                source=ErrorSource.FRONTEND,
                exception_class=name,
                path=path,
                signature=signature,
            ),
            level="error",
            exception_class=name[:255],
            message=message,
            traceback=utils.truncate(str(data["stack"]), utils.MAX_TRACEBACK),
            path=path[:512],
            user_agent=request.META.get("HTTP_USER_AGENT", "")[: utils.MAX_USER_AGENT],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("actor_type", str, enum=["anonymous", "demo", "user"], required=False),
            OpenApiParameter("method", str, enum=["POST", "PUT", "PATCH", "DELETE"], required=False),
            OpenApiParameter("status_code", int, required=False),
            OpenApiParameter("search", str, required=False),
        ]
    )
)
class AuditEventViewSet(ReadOnlyModelViewSet[AuditEvent]):
    """Read-only audit feed for the /ops console — superuser only.

    ``[IsAuthenticated, IsSuperUser]`` is set EXPLICITLY so it replaces the global
    ``DemoReadOnly`` default: anonymous/demo/non-super all 403, owner 200. List filters
    (``?actor_type=&method=&status_code=&search=``) apply on the list action only — the
    list-only guard from the read-API convention, so a stray param can't 404 a retrieve."""

    serializer_class = AuditEventSerializer
    permission_classes = [IsAuthenticated, IsSuperUser]
    queryset = AuditEvent.objects.all()

    def get_queryset(self) -> QuerySet[AuditEvent]:
        qs = AuditEvent.objects.all().order_by("-created_at", "-id")
        if self.action != "list":
            return qs
        params = self.request.query_params
        actor_type = params.get("actor_type")
        if actor_type:
            qs = qs.filter(actor_type=actor_type)
        method = params.get("method")
        if method:
            qs = qs.filter(method=method.upper())
        status_code = params.get("status_code")
        if status_code and status_code.isdigit():
            qs = qs.filter(status_code=int(status_code))
        search = params.get("search")
        if search:
            qs = qs.filter(
                Q(path__icontains=search)
                | Q(view_name__icontains=search)
                | Q(actor_username__icontains=search)
            )
        return qs


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("source", str, enum=["backend", "frontend"], required=False),
        ]
    )
)
class ErrorGroupViewSet(mixins.ListModelMixin, GenericViewSet[Any]):
    """Fingerprint-grouped error triage for the /ops console — superuser only.

    Each row is a distinct ``(fingerprint, source, exception_class)`` with its dedup count,
    first/last-seen, and the latest occurrence's representative message/path. Computed in
    ``get_queryset`` (a Python list, the ``MoversViewSet`` precedent) and paginated by the
    list mixin; bounded by the 90-day retention prune + low traffic, so all groups fit in
    memory. ``?source=`` filters backend vs frontend."""

    serializer_class = ErrorGroupSerializer
    permission_classes = [IsAuthenticated, IsSuperUser]

    def get_queryset(self) -> QuerySet[Any]:
        groups = (
            ErrorLog.objects.values("fingerprint", "source", "exception_class")
            .annotate(
                count=Count("id"),
                first_seen=Min("created_at"),
                last_seen=Max("created_at"),
                last_id=Max("id"),
            )
            .order_by("-last_seen", "fingerprint")
        )
        source = self.request.query_params.get("source")
        if source:
            groups = groups.filter(source=source)
        return groups

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        # Paginate the aggregate at the DB level (LIMIT/OFFSET) so only the current page's
        # groups are materialized — NOT the full distinct-fingerprint set, whose cardinality
        # is partly driven by the public client-error beacon. Then one extra query fetches
        # the page's representative latest-occurrence message/path.
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        rows = list(page) if page is not None else list(queryset)
        reps = {
            e.id: e
            for e in ErrorLog.objects.filter(id__in=[row["last_id"] for row in rows])
        }
        shaped = [self._shape(row, reps.get(row["last_id"])) for row in rows]
        serializer = self.get_serializer(shaped, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @staticmethod
    def _shape(row: dict[str, Any], rep: ErrorLog | None) -> dict[str, Any]:
        return {
            "fingerprint": row["fingerprint"],
            "source": row["source"],
            "exception_class": row["exception_class"],
            "count": row["count"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "message": rep.message if rep else "",
            "path": rep.path if rep else "",
            "status_code": rep.status_code if rep else None,
        }
