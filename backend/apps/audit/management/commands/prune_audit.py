from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.audit.models import AuditEvent, ErrorLog, ErrorSource


class Command(BaseCommand):
    """Delete audit events and error logs past their retention windows.

    Run by a daily systemd timer (NOT write-time, unlike the host-metrics collector whose
    collector *is* the timer): the audit middleware runs on the hot request path, so a
    per-write DELETE would add contention to every mutation. Retention is governed by
    ``AUDIT_EVENT_RETENTION_DAYS`` (audit), ``ERROR_LOG_RETENTION_DAYS`` (backend errors)
    and the shorter ``FRONTEND_ERROR_LOG_RETENTION_DAYS`` (the public-beacon frontend
    errors — cheapest to abuse, least valuable to keep long)."""

    help = "Prune audit events and error logs older than their retention windows."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        audit_cutoff = now - timedelta(days=settings.AUDIT_EVENT_RETENTION_DAYS)
        backend_cutoff = now - timedelta(days=settings.ERROR_LOG_RETENTION_DAYS)
        frontend_cutoff = now - timedelta(days=settings.FRONTEND_ERROR_LOG_RETENTION_DAYS)

        audit_deleted, _ = AuditEvent.objects.filter(created_at__lt=audit_cutoff).delete()
        frontend_deleted, _ = ErrorLog.objects.filter(
            source=ErrorSource.FRONTEND, created_at__lt=frontend_cutoff
        ).delete()
        backend_deleted, _ = (
            ErrorLog.objects.exclude(source=ErrorSource.FRONTEND)
            .filter(created_at__lt=backend_cutoff)
            .delete()
        )

        self.stdout.write(
            f"pruned {audit_deleted} audit events (>{settings.AUDIT_EVENT_RETENTION_DAYS}d), "
            f"{frontend_deleted} frontend errors (>{settings.FRONTEND_ERROR_LOG_RETENTION_DAYS}d), "
            f"{backend_deleted} backend errors (>{settings.ERROR_LOG_RETENTION_DAYS}d)"
        )
