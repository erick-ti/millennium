from __future__ import annotations

import hashlib
import hmac
import re

import structlog
from django.conf import settings

from apps.audit.models import ActorType
from apps.core.permissions import is_demo_user

# Truncation caps — one pathological payload must never bloat a row. Generous enough
# to keep a real traceback intact, bounded enough to keep the table lean.
MAX_MESSAGE = 2_000
MAX_TRACEBACK = 20_000
MAX_USER_AGENT = 512

# Collapse numeric path segments so /api/decks/1/ and /api/decks/2/ fingerprint together.
_NUMERIC_SEGMENT_RE = re.compile(r"/\d+(?=/|$)")


def hash_session_key(session_key: str | None) -> str:
    """HMAC-SHA256 of the session key, keyed by ``SECRET_KEY``.

    Stored instead of the raw key so the audit table cannot be used to hijack a live
    session if it leaks; keyed (not a bare digest) so it is not precomputable and is
    scoped to this deployment. Deterministic, so the ``/ops`` console can still GROUP a
    session's events. Empty string for an anonymous (sessionless) request."""
    if not session_key:
        return ""
    return hmac.new(
        settings.SECRET_KEY.encode(), session_key.encode(), hashlib.sha256
    ).hexdigest()


def classify_actor(user: object) -> str:
    """Map a request user to an :class:`ActorType` value (anonymous/demo/user)."""
    if not getattr(user, "is_authenticated", False):
        return ActorType.ANONYMOUS
    if is_demo_user(user):
        return ActorType.DEMO
    return ActorType.USER


def current_request_id() -> str:
    """The per-request id ``django_structlog`` binds to structlog contextvars, so audit
    rows correlate with the JSON application logs. Empty if no request is bound."""
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    return str(request_id)[:64] if request_id else ""


def normalize_path(path: str) -> str:
    """Replace numeric id segments with ``/:id`` so per-resource paths fingerprint as one."""
    return _NUMERIC_SEGMENT_RE.sub("/:id", path or "")


def truncate(text: str, limit: int) -> str:
    """Truncate ``text`` to ``limit`` characters, appending an ellipsis marker if cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + "…[truncated]"


def fingerprint_error(
    *, source: str, exception_class: str, path: str, signature: str
) -> str:
    """Stable grouping key: hash of source + exception class + id-normalized path +
    signature (the first line of the message / top frame). Same bug at different ids →
    same fingerprint; a different exception or route → a different one."""
    basis = "|".join(
        [
            source,
            exception_class or "",
            normalize_path(path),
            (signature or "")[:200],
        ]
    )
    return hashlib.sha256(basis.encode()).hexdigest()
