from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Liveness probe — returns 200 when the process is up."""

    permission_classes = [AllowAny]
    authentication_classes: list[type] = []

    @extend_schema(
        summary="Service health",
        responses={200: OpenApiResponse(description="Service is healthy")},
    )
    def get(self, request: Request) -> Response:
        return Response({"status": "ok"}, status=status.HTTP_200_OK)


@method_decorator(ensure_csrf_cookie, name="get")
class CsrfView(APIView):
    """Seed the ``csrftoken`` cookie (slice 6, DECISIONS 2026-05-29).

    Django sets the cookie only when a request *uses* the token (``get_token``);
    with ``CSRF_USE_SESSIONS=False`` and an all-JSON API that never renders a
    form, nothing here was setting it — so the SPA had no token to send on its
    first POST. The frontend GETs this on load; ``CsrfViewMiddleware`` then writes
    the (non-HttpOnly) cookie, and ``proxy.ts`` copies it into ``X-CSRFToken`` on
    unsafe requests. ``AllowAny`` + no auth: a not-yet-signed-in browser must be
    able to seed the cookie, and a CSRF cookie leaks nothing (it's a per-session
    secret the client already holds). Safe method → no CSRF *enforcement* here."""

    permission_classes = [AllowAny]
    authentication_classes: list[type] = []

    @extend_schema(
        summary="Seed the CSRF cookie",
        responses={200: OpenApiResponse(description="csrftoken cookie set")},
    )
    def get(self, request: Request) -> Response:
        return Response({"detail": "CSRF cookie set"}, status=status.HTTP_200_OK)
