from typing import cast

from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.serializers import LoginSerializer, UserSerializer


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


@method_decorator(csrf_protect, name="post")
class LoginView(APIView):
    """Establish a session for valid credentials (Phase 5 auth slice).

    ``AllowAny`` + no authenticators so an anonymous browser can reach it (the
    ``HealthView``/``CsrfView`` precedent — every other endpoint stays
    ``IsAuthenticated``). The credential check + status choice live in
    ``LoginSerializer`` (a failure is a generic 400, see its docstring).

    ``csrf_protect`` re-arms CSRF on this POST: DRF marks every ``APIView``
    ``csrf_exempt`` because CSRF normally runs inside
    ``SessionAuthentication.enforce_csrf`` — which an *anonymous* request never
    reaches (it returns before the check). So without this decorator the login
    POST would be silently CSRF-naked. The ``csrftoken`` is already seeded by
    ``GET /api/csrf/`` on app load and echoed via ``proxy.ts``'s ``X-CSRFToken``
    (slice 6), so this composes with zero new frontend plumbing."""

    permission_classes = [AllowAny]
    authentication_classes: list[type] = []
    # Brute-force speed bump on the one anonymous credential surface — CSRF stops
    # cross-site logins but not a direct client that seeds /api/csrf/ itself. Rate
    # in DEFAULT_THROTTLE_RATES["login"]; over the limit → 429 (single-user app, so
    # one global bucket behind the proxy is fine — see the settings note).
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(
        summary="Log in (establish a session cookie)",
        request=LoginSerializer,
        responses={
            200: UserSerializer,
            400: OpenApiResponse(description="Missing fields or invalid credentials"),
            429: OpenApiResponse(description="Too many login attempts — retry later"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        login(request._request, user)
        return Response(UserSerializer(user).data, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """Clear the session (Phase 5 auth slice).

    POST (an unsafe method) deliberately, so it travels the *authenticated* CSRF
    path: the caller is authenticated, so ``SessionAuthentication.enforce_csrf``
    runs and ``proxy.ts`` already injects ``X-CSRFToken`` — no ``csrf_protect``
    needed here (unlike login). Inherits the global ``IsAuthenticated``, so an
    anonymous logout 403s like everything else. Returns 200 with a body (not 204)
    so the generated TS client has a typed, non-void response to branch on."""

    @extend_schema(
        summary="Log out (clear the session)",
        request=None,
        responses={200: OpenApiResponse(description="Logged out")},
    )
    def post(self, request: Request) -> Response:
        logout(request._request)
        return Response({"detail": "Logged out."}, status=status.HTTP_200_OK)


class MeView(APIView):
    """The current authenticated user (Phase 5 auth slice).

    Inherits the global ``IsAuthenticated``, so an anonymous request → **403**
    (DRF's session-auth posture: ``authenticate_header`` is ``None``, so a 401
    downgrades to 403). The SPA's ``AuthProvider`` reads that 403 as "not signed
    in" — it is the expected anonymous signal, not an error to surface."""

    @extend_schema(
        summary="Current authenticated user",
        responses={200: UserSerializer},
    )
    def get(self, request: Request) -> Response:
        # IsAuthenticated guarantees an authenticated User here (never AnonymousUser).
        return Response(
            UserSerializer(cast(User, request.user)).data, status=status.HTTP_200_OK
        )
