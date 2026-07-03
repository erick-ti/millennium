from __future__ import annotations

import time
import traceback as traceback_mod
from collections.abc import Callable
from typing import Any

import structlog
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import Http404, HttpRequest, HttpResponse

from apps.audit import utils
from apps.audit.models import ActorType, AuditEvent, ErrorLog, ErrorSource

logger = structlog.get_logger("apps.audit")

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# View names this middleware must NOT audit (noise / self-reference). The frontend error
# beacon is added in the intake slice; recording its writes would flood the audit trail
# with error-report traffic that the ErrorLog already captures.
_SKIP_VIEW_NAMES = frozenset({"client-error"})

# Public 4xx requests are normally skipped (unbounded no-op probes on unthrottled endpoints),
# but the login endpoint is the credential-attack surface: its bounded failures (bad creds
# 400 / throttled 429) ARE a security signal worth the /ops trail, and login is throttled
# (5/min), so auditing them can't be a growth vector.
_AUDIT_ALWAYS_VIEW_NAMES = frozenset({"core:auth-login"})


class AuditMiddleware:
    """Record an :class:`AuditEvent` for every mutating request and an :class:`ErrorLog`
    for every backend exception / 5xx.

    Registered LAST in ``MIDDLEWARE`` (inside ``django_structlog``'s ``RequestMiddleware``)
    so that, by the time it runs, ``request.user`` (AuthenticationMiddleware) and
    ``request.session`` (SessionMiddleware) are populated and the per-request
    ``request_id`` is bound to structlog contextvars. A logging failure is swallowed —
    observability must never break the request it observes.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        start = time.monotonic()
        # Snapshot the actor/session BEFORE the view runs. login() sets request.user during
        # the view (post-view is the right actor), while logout() flushes the session and
        # resets request.user to AnonymousUser (pre-view is the right actor + session).
        # _record_event picks the authenticated side of {pre, post}. Unsafe methods only.
        is_unsafe = request.method in _UNSAFE_METHODS
        pre_identity = (
            self._identity(request, getattr(request, "user", None)) if is_unsafe else None
        )
        response = self.get_response(request)

        if is_unsafe and pre_identity is not None:
            try:
                self._record_event(request, response, start, pre_identity)
            except Exception:  # pragma: no cover - defensive; never break the request
                logger.warning("audit_event_record_failed", exc_info=True)

        # A 5xx that did NOT raise (e.g. a hand-rendered 500) still deserves an ErrorLog;
        # process_exception already covered the raised case and set the dedupe flag.
        if response.status_code >= 500 and not getattr(
            request, "_audit_error_recorded", False
        ):
            try:
                self._record_error(request, response=response)
            except Exception:  # pragma: no cover - defensive
                logger.warning("audit_error_record_failed", exc_info=True)

        return response

    def process_exception(self, request: HttpRequest, exception: Exception) -> None:
        # Http404 / PermissionDenied / SuspiciousOperation are expected control flow that
        # Django renders as a normal 4xx (404 / 403 / 400), NOT errors (django_structlog's
        # RequestMiddleware skips the first two for the same reason). Recording them would
        # pollute the /ops feed with phantom "500"s AND — for SuspiciousOperation, whose
        # RequestDataTooBig subclass an oversized public beacon raises — let a source=backend
        # row dodge the frontend quota + retention split. Genuine
        # 500s are unaffected (the __call__ 5xx branch covers them).
        if isinstance(exception, (Http404, PermissionDenied, SuspiciousOperation)):
            return None
        try:
            self._record_error(request, exception=exception)
            request._audit_error_recorded = True  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - defensive
            logger.warning("audit_error_record_failed", exc_info=True)
        return None

    # -- internals ---------------------------------------------------------------

    @staticmethod
    def _session_key(request: HttpRequest) -> str | None:
        session = getattr(request, "session", None)
        return getattr(session, "session_key", None) if session is not None else None

    def _identity(self, request: HttpRequest, user: object) -> dict[str, Any]:
        """Snapshot the AuditEvent actor/session fields from a given user + the request's
        CURRENT session key (so a pre-view call captures the pre-flush session)."""
        authed = bool(getattr(user, "is_authenticated", False))
        return {
            "actor": user if authed else None,
            "actor_type": utils.classify_actor(user),
            "actor_username": getattr(user, "username", "") if authed else "",
            "session_key_hash": utils.hash_session_key(self._session_key(request)),
        }

    def _record_event(
        self,
        request: HttpRequest,
        response: HttpResponse,
        start: float,
        pre_identity: dict[str, Any],
    ) -> None:
        resolver_match = getattr(request, "resolver_match", None)
        view_name = (getattr(resolver_match, "view_name", "") or "")
        if view_name in _SKIP_VIEW_NAMES:
            return

        # login() authenticates during the view (post is the right actor); logout()
        # de-authenticates (pre is right). Pick whichever side is authenticated — otherwise
        # they agree — so a logout keeps the real actor/session instead of AnonymousUser.
        post_identity = self._identity(request, getattr(request, "user", None))
        identity = (
            post_identity
            if post_identity["actor_type"] != ActorType.ANONYMOUS
            else pre_identity
        )
        actor_type = identity["actor_type"]

        # The read-only DEMO showcase is a PUBLIC surface: its only possible actions are
        # session lifecycle (demo-login is a 30/min public ~43k-rows/day growth vector; demo
        # logout) and blocked 403s — none mutate data or are the owner, so NO demo action is a
        # meaningful audit event. Skip the actor entirely; this also
        # closes the demo-login write-amplification into the 365-day audit table.
        if actor_type == ActorType.DEMO:
            return

        # Skip an ANONYMOUS request that FAILED (4xx): every write endpoint is
        # IsAuthenticated, so a public 4xx is a no-op probe on an UNTHROTTLED endpoint, and
        # recording it would let a public loop grow the audit table and bury the real trail.
        # EXCEPT the throttled login surface, whose bounded failures are a credential-attack
        # signal. Real-account writes (owner) and a public request that RESOLVES to a real
        # account (a login that succeeds → actor becomes the owner) are still recorded.
        if (
            actor_type == ActorType.ANONYMOUS
            and response.status_code >= 400
            and view_name not in _AUDIT_ALWAYS_VIEW_NAMES
        ):
            return

        kwargs = dict(getattr(resolver_match, "kwargs", {}) or {})
        object_id = str(kwargs.get("pk") or kwargs.get("id") or "")

        AuditEvent.objects.create(
            **identity,
            request_id=utils.current_request_id(),
            method=(request.method or "")[:8],
            path=request.path[:512],
            view_name=view_name[:255],
            status_code=response.status_code,
            object_id=object_id[:64],
            # Allowlist: route kwargs are ids/slugs from the URL, never request-body data.
            detail={"route_kwargs": {k: str(v)[:128] for k, v in kwargs.items()}},
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    def _record_error(
        self,
        request: HttpRequest,
        *,
        exception: Exception | None = None,
        response: HttpResponse | None = None,
    ) -> None:
        if exception is not None:
            exception_class = type(exception).__name__
            message = str(exception) or exception_class
            tb_text = "".join(
                traceback_mod.format_exception(
                    type(exception), exception, exception.__traceback__
                )
            )
            status_code: int | None = 500
        else:
            assert response is not None
            exception_class = ""
            message = f"HTTP {response.status_code}"
            tb_text = ""
            status_code = response.status_code

        user = getattr(request, "user", None)
        authed = bool(getattr(user, "is_authenticated", False))
        signature = message.splitlines()[0] if message else exception_class

        ErrorLog.objects.create(
            source=ErrorSource.BACKEND,
            actor=user if authed else None,
            actor_type=utils.classify_actor(user),
            actor_username=(getattr(user, "username", "") if authed else ""),
            session_key_hash=utils.hash_session_key(self._session_key(request)),
            request_id=utils.current_request_id(),
            fingerprint=utils.fingerprint_error(
                source=ErrorSource.BACKEND,
                exception_class=exception_class,
                path=request.path,
                signature=signature,
            ),
            level="error",
            exception_class=exception_class[:255],
            message=utils.truncate(message, utils.MAX_MESSAGE),
            traceback=utils.truncate(tb_text, utils.MAX_TRACEBACK),
            path=request.path[:512],
            method=(request.method or "")[:8],
            status_code=status_code,
            user_agent=request.META.get("HTTP_USER_AGENT", "")[: utils.MAX_USER_AGENT],
        )
