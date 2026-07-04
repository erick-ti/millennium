from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.audit.models import AuditEvent, ErrorLog


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin[AuditEvent]):
    list_display = ["created_at", "actor_type", "actor_username", "method", "path", "status_code"]
    list_filter = ["actor_type", "method", "status_code"]
    search_fields = ["path", "view_name", "actor_username", "request_id"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
    readonly_fields = [
        "actor",
        "actor_type",
        "actor_username",
        "session_key_hash",
        "request_id",
        "method",
        "path",
        "view_name",
        "status_code",
        "object_type",
        "object_id",
        "changed_fields",
        "detail",
        "duration_ms",
        "created_at",
        "updated_at",
    ]

    # Append-only (the SyncRunAdmin posture): machine-written rows are never hand-edited
    # or deleted. The obj=None (model-level) change check still defers to Django's
    # permission so the changelist stays gated — never hard-code True there.
    def has_delete_permission(self, request: HttpRequest, obj: AuditEvent | None = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: AuditEvent | None = None) -> bool:
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)


@admin.register(ErrorLog)
class ErrorLogAdmin(admin.ModelAdmin[ErrorLog]):
    list_display = ["created_at", "source", "exception_class", "status_code", "path", "actor_type"]
    list_filter = ["source", "actor_type", "status_code"]
    search_fields = ["exception_class", "message", "path", "fingerprint", "request_id"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
    readonly_fields = [
        "source",
        "actor",
        "actor_type",
        "actor_username",
        "session_key_hash",
        "request_id",
        "fingerprint",
        "level",
        "exception_class",
        "message",
        "traceback",
        "path",
        "method",
        "status_code",
        "user_agent",
        "extra",
        "created_at",
        "updated_at",
    ]

    def has_delete_permission(self, request: HttpRequest, obj: ErrorLog | None = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: ErrorLog | None = None) -> bool:
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)
