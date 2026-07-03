from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class ActorType(models.TextChoices):
    """Who performed the request, by session posture (single-tenant app).

    ``anonymous`` = no authenticated session; ``demo`` = the read-only showcase
    account (``apps.core.permissions.is_demo_user``); ``user`` = a real authenticated
    account (the owner). Pure descriptive telemetry written by ``AuditMiddleware`` —
    NOT a natural key or a guarded column, so it carries no DB ``CheckConstraint``
    (the open-vocabulary ``Card.archetype`` precedent, not the closed-enum
    ``SyncRun.kind`` one): a future actor type can be added without an ``ALTER`` of a
    CHECK, and a stray value is cosmetic, never a corruption vector.
    """

    ANONYMOUS = "anonymous", "Anonymous"
    DEMO = "demo", "Demo"
    USER = "user", "User"


class ErrorSource(models.TextChoices):
    """Where an ``ErrorLog`` originated: a backend exception/5xx vs. a frontend
    error beacon (``POST /api/client-errors/``, added in the intake slice)."""

    BACKEND = "backend", "Backend"
    FRONTEND = "frontend", "Frontend"


class AuditEvent(TimeStampedModel):
    """Append-only record of one mutating request — *who did what, when, with what outcome*.

    Written by ``apps.audit.middleware.AuditMiddleware`` for every unsafe method
    (POST/PUT/PATCH/DELETE) across every endpoint, so a new write surface can't silently
    escape the audit trail (the ``DemoReadOnly`` global-chokepoint reasoning, applied to
    observability). Follows the ``SyncRun`` append-only posture: inserted, never updated;
    the admin blocks edit/delete.

    Deliberately privacy-bounded (review feedback 2026-06-21):
    - the raw Django ``session_key`` is NEVER stored — only ``session_key_hash`` (an
      HMAC-SHA256, so the table can't be used to hijack a live session if it leaks),
    - ``detail`` is a strict allowlist (route kwargs only), NEVER the request body — so
      uploaded CSVs, credentials, CSRF tokens, and emails never land here.

    ``request_id`` is the same id ``django_structlog`` binds per request, so a row here
    correlates with the JSON application logs in journald and with any ``ErrorLog`` from
    the same request.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    actor_type = models.CharField(
        max_length=16, choices=ActorType.choices, default=ActorType.ANONYMOUS
    )
    # Denormalized so the actor survives a (single-tenant: unlikely) user deletion that
    # SET_NULLs the FK — non-sensitive (the owner's own username / the literal "demo").
    actor_username = models.CharField(max_length=150, blank=True, default="")
    session_key_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    request_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    method = models.CharField(max_length=8)
    # request.path only — never the query string (avoids logging a token passed as ?param).
    path = models.CharField(max_length=512)
    view_name = models.CharField(max_length=255, blank=True, default="")
    status_code = models.PositiveSmallIntegerField()
    object_type = models.CharField(max_length=120, blank=True, default="")
    object_id = models.CharField(max_length=64, blank=True, default="")
    # Field NAMES only (never values) — populated by opt-in view instrumentation later;
    # the generic middleware leaves it empty because it does not parse request bodies.
    changed_fields = models.JSONField(default=list, blank=True)
    # Strict allowlist (route kwargs). NEVER the request body.
    detail = models.JSONField(default=dict, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["-created_at", "-id"], name="audit_event_recent_idx"),
            models.Index(fields=["actor", "-created_at"], name="audit_event_actor_idx"),
            models.Index(fields=["status_code", "-created_at"], name="audit_event_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.method} {self.path} -> {self.status_code} ({self.actor_type})"


class ErrorLog(TimeStampedModel):
    """Append-only record of one error — a backend exception/5xx or a frontend beacon.

    Individual occurrences are retained (so each keeps its own ``request_id``/actor for
    correlation), and a ``fingerprint`` (stable hash of source + exception class +
    id-normalized path + signature) lets the ``/ops`` console GROUP occurrences for a
    deduped view without losing the per-occurrence detail a mutable aggregate would drop.

    Text fields are truncated at write time (``apps.audit.utils``) so one pathological
    traceback can't bloat a row; ``extra`` is an allowlist (never raw cookies / storage).
    """

    source = models.CharField(max_length=16, choices=ErrorSource.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="error_logs",
    )
    actor_type = models.CharField(
        max_length=16, choices=ActorType.choices, default=ActorType.ANONYMOUS
    )
    actor_username = models.CharField(max_length=150, blank=True, default="")
    session_key_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    request_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    fingerprint = models.CharField(max_length=64, db_index=True)
    level = models.CharField(max_length=16, blank=True, default="error")
    exception_class = models.CharField(max_length=255, blank=True, default="")
    message = models.TextField(blank=True, default="")
    traceback = models.TextField(blank=True, default="")
    path = models.CharField(max_length=512, blank=True, default="")
    method = models.CharField(max_length=8, blank=True, default="")
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True, default="")
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["fingerprint", "-created_at"], name="error_log_fingerprint_idx"),
            models.Index(fields=["source", "-created_at"], name="error_log_source_idx"),
            models.Index(fields=["-created_at", "-id"], name="error_log_recent_idx"),
        ]

    def __str__(self) -> str:
        label = self.exception_class or f"HTTP {self.status_code}"
        return f"{self.source} {label} ({self.fingerprint[:8]})"
