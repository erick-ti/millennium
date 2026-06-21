from typing import cast

from django.contrib.auth import get_user, login, logout
from django.contrib.auth.models import User
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.permissions import DEMO_USERNAME, is_demo_user
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


@method_decorator(csrf_protect, name="post")
class DemoLoginView(APIView):
    """Establish a session for the read-only demo account (recruiter showcase).

    A public, password-less counterpart to ``LoginView``: it ``login()``s the seeded
    ``demo`` account (``DEMO_USERNAME``) so a recruiter reaches the full authenticated
    app in one click, while ``DemoReadOnly`` (a global default permission) denies that
    session every unsafe method. ``AllowAny`` + no authenticators (the ``LoginView``
    precedent) so an anonymous browser can reach it; ``csrf_protect`` re-arms CSRF on the
    POST (a bare ``APIView`` is ``csrf_exempt`` and ``SessionAuthentication`` skips the
    check for anonymous requests, so it would otherwise be CSRF-naked), composing with the
    existing ``GET /api/csrf/`` + ``proxy.ts`` ``X-CSRFToken`` flow with zero new frontend
    plumbing. Its OWN throttle scope (``demo_login``), never the ``login`` bucket, so demo
    traffic can't drain the credential speed bump. **404** when no demo account is seeded
    (dev / test, or before ``ensure_demo_user`` has run on the box) so the SPA can degrade
    gracefully rather than 500."""

    permission_classes = [AllowAny]
    authentication_classes: list[type] = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "demo_login"
    # Bound a demo session's life so a leaked/abandoned one isn't valid for the default two
    # weeks, and its django_session row turns collectable sooner. 8h is roomy for a recruiter
    # to explore. (Full reclamation still needs a scheduled `clearsessions` — a deploy-side
    # follow-up; this caps validity in-app regardless.)
    DEMO_SESSION_SECONDS = 60 * 60 * 8

    @extend_schema(
        summary="Log in to the read-only demo account",
        request=None,
        responses={
            200: UserSerializer,
            404: OpenApiResponse(description="No demo account is available"),
            429: OpenApiResponse(description="Too many demo sign-ins — retry later"),
        },
    )
    def post(self, request: Request) -> Response:
        # Never downgrade an existing OWNER session to the demo. This endpoint runs with
        # authentication_classes=[], and DRF's Request.user setter overwrites
        # request._request.user to AnonymousUser — so read the SESSION directly with
        # django.contrib.auth.get_user (it returns the session's user regardless of DRF). A
        # logged-in owner can reach here if a transient /api/auth/me probe failure made the SPA
        # show the demo CTA (AuthProvider collapses any /me error to "anonymous") — return the
        # owner untouched rather than replacing their session (Codex review 2026-06-21). A demo
        # session re-clicking falls through and harmlessly re-establishes the demo.
        current = get_user(request._request)
        if current.is_authenticated and not is_demo_user(current):
            return Response(UserSerializer(current).data, status=status.HTTP_200_OK)
        try:
            user = User.objects.get(username=DEMO_USERNAME, is_active=True)
        except User.DoesNotExist:
            return Response(
                {"detail": "The demo is not available."},
                status=status.HTTP_404_NOT_FOUND,
            )
        # Provenance guard — verify the FULL safe posture at request time, never trusting
        # that ensure_demo_user's seed hasn't drifted (the seed_smoke precedent). The
        # seed-owned demo is unprivileged (no staff/superuser) AND password-less; refuse to
        # mint a public session for anything else named "demo": a usable password (a
        # hand-created account that must sign in normally) OR any privilege. The privilege
        # half is load-bearing even though the API is otherwise demo-locked — DemoReadOnly /
        # IsNotDemoUser key on the username, but /admin/ is mounted OUTSIDE DRF and shares
        # the session cookie, so a password-less staff/superuser "demo" entered here would
        # reach the admin (Codex adversarial review 2026-06-21).
        if user.is_staff or user.is_superuser or user.has_usable_password():
            return Response(
                {"detail": "The demo is not available."},
                status=status.HTTP_404_NOT_FOUND,
            )
        # Programmatic login() needs an explicit backend: we fetched the user via the ORM
        # (not authenticate()), so user.backend isn't set. The project runs the single
        # default ModelBackend.
        login(
            request._request,
            user,
            backend="django.contrib.auth.backends.ModelBackend",
        )
        request._request.session.set_expiry(self.DEMO_SESSION_SECONDS)
        return Response(UserSerializer(user).data, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """Clear the session (Phase 5 auth slice).

    POST (an unsafe method) deliberately, so it travels the *authenticated* CSRF
    path: the caller is authenticated, so ``SessionAuthentication.enforce_csrf``
    runs and ``proxy.ts`` already injects ``X-CSRFToken`` — no ``csrf_protect``
    needed here (unlike login). Returns 200 with a body (not 204) so the generated
    TS client has a typed, non-void response to branch on.

    Sets ``permission_classes = [IsAuthenticated]`` to OPT OUT of the global
    ``DemoReadOnly`` write-block: logout is the one unsafe method the demo account
    must be allowed (the ``LogoutButton`` hard-navigates to ``/login`` on a 200; a 403
    would strand the recruiter in the demo session). Still requires auth, so an
    anonymous logout 403s like everything else."""

    permission_classes = [IsAuthenticated]

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
