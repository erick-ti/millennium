from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework.permissions import SAFE_METHODS, BasePermission

if TYPE_CHECKING:
    # Type-only: importing these at module scope triggers a circular import when DRF
    # resolves DEFAULT_PERMISSION_CLASSES mid-init (rest_framework.views is partially
    # initialized at that point). `from __future__ import annotations` makes the hints
    # lazy strings, so they're never needed at runtime.
    from rest_framework.request import Request
    from rest_framework.views import APIView

# The read-only showcase account. A recruiter reaches the full authenticated app in
# one click via POST /api/auth/demo-login/ (which login()s this user); DemoReadOnly
# then denies that session every unsafe method, so the demo browses the owner's real
# data but can never mutate it. This is the single, backend-only source of truth for the
# username, shared by the permission, the DemoLoginView, and the ensure_demo_user
# command. The FRONTEND does NOT hard-code it — it reads the ``is_demo`` flag the
# UserSerializer derives from ``is_demo_user`` (see apps/core/serializers.py), so there
# is no cross-side literal to drift.
DEMO_USERNAME = "demo"


def is_demo_user(user: object) -> bool:
    """True only for the seed-owned read-only demo account: an *authenticated* user named
    ``DEMO_USERNAME`` that is **password-less**.

    The password-less requirement keys identity on the seed posture (``ensure_demo_user``
    creates it with an unusable password), NOT the username alone — so a real account that
    merely reuses the reserved "demo" name (and keeps its password) is NOT misclassified as
    the showcase and silently read-only-locked. This mirrors ``DemoLoginView``'s own
    password-less guard, so a passworded "demo" is consistently treated as a normal account
    everywhere (Codex review 2026-06-21). False for ``AnonymousUser`` and every real account."""
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "username", None) != DEMO_USERNAME:
        return False
    has_usable_password = getattr(user, "has_usable_password", None)
    return callable(has_usable_password) and not has_usable_password()


class DemoReadOnly(BasePermission):
    """Block every unsafe method for the demo account; a no-op for everyone else.

    Added to ``DEFAULT_PERMISSION_CLASSES`` alongside ``IsAuthenticated`` (DRF ANDs
    them), so it is the ONE chokepoint that read-only-locks the demo session across
    every endpoint — a future write viewset can't silently miss it (the per-viewset
    enumeration alternative is the corruption hole the design review flagged). Being
    method-based it also covers the imports ``@action`` POSTs (approve/override/reject),
    which a model-action allowlist would miss. Purely additive: it can only DENY
    (``IsAuthenticated`` still gates auth), so the fail-closed posture is preserved and
    it loosens nothing.

    The only write the demo account may perform is logout — ``LogoutView`` opts out
    (``permission_classes = [IsAuthenticated]``) so a 403 never strands the recruiter in
    the demo session. The ``AllowAny`` endpoints (health / csrf / login / demo-login)
    don't inherit the defaults at all.
    """

    message = "The demo account is read-only. Sign in to make changes."

    def has_permission(self, request: Request, view: APIView) -> bool:
        if request.method in SAFE_METHODS:
            return True
        return not is_demo_user(request.user)


class IsNotDemoUser(BasePermission):
    """Authenticated, and NOT the read-only demo account.

    Backs ``SPECTACULAR_SETTINGS["SERVE_PERMISSIONS"]`` so the OpenAPI schema + Swagger
    docs stay reachable only by the real owner (Invariant 7 — the schema is recon
    material for a private app). The demo account is otherwise an authenticated session,
    so plain ``IsAuthenticated`` would let it read the full machine-readable API surface;
    this keeps it out. Still requires auth (anonymous → 403, as the invariant demands) —
    it only ALSO excludes the demo, a strengthening, not a loosening. The SPA fetches the
    schema offline at build time, so gating the demo here has no runtime UX cost."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        return bool(
            getattr(request.user, "is_authenticated", False)
            and not is_demo_user(request.user)
        )
