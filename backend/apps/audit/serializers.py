from __future__ import annotations

from rest_framework import serializers

from apps.audit.models import AuditEvent


class ClientErrorSerializer(serializers.Serializer[dict[str, object]]):
    """Validate a frontend error beacon (``POST /api/audit/client-errors/``).

    A STRICT allowlist: only these fields are accepted, and there is deliberately NO
    free-form ``extra`` blob — so cookies, localStorage/sessionStorage, or arbitrary
    payloads can never be exfiltrated into the error store (review feedback 2026-06-21).
    Fields are accepted leniently (no ``max_length`` → no 400 on a long stack) and
    TRUNCATED server-side; ``message`` is the one required field. ``request_id`` carries
    the ``X-Request-ID`` of the failed API call when the SPA has it, so a frontend error
    correlates back to the backend request that triggered it."""

    message = serializers.CharField(allow_blank=False, trim_whitespace=False)
    name = serializers.CharField(required=False, allow_blank=True, default="")
    stack = serializers.CharField(required=False, allow_blank=True, default="")
    url = serializers.CharField(required=False, allow_blank=True, default="")
    request_id = serializers.CharField(required=False, allow_blank=True, default="")


class AuditEventSerializer(serializers.ModelSerializer[AuditEvent]):
    """Read serializer for the /ops audit feed. The raw ``session_key_hash`` is
    intentionally NOT exposed — it is an internal grouping key, not display data."""

    class Meta:
        model = AuditEvent
        fields = [
            "id",
            "created_at",
            "actor_type",
            "actor_username",
            "method",
            "path",
            "view_name",
            "status_code",
            "object_type",
            "object_id",
            "request_id",
            "duration_ms",
            "detail",
        ]


class ErrorGroupSerializer(serializers.Serializer[dict[str, object]]):
    """One fingerprint-grouped error row for the /ops triage view: the dedup count +
    first/last seen, plus the latest occurrence's representative message/path."""

    fingerprint = serializers.CharField()
    # "source" shadows DRF's inherited Field.source attribute (Serializer subclasses Field),
    # which mypy flags as an incompatible override. Runtime is correct — the field binds with
    # an implicit source="source" and reads the "source" dict key — so silence just the typing.
    source = serializers.CharField()  # type: ignore[assignment]
    exception_class = serializers.CharField(allow_blank=True)
    count = serializers.IntegerField()
    first_seen = serializers.DateTimeField()
    last_seen = serializers.DateTimeField()
    message = serializers.CharField(allow_blank=True)
    path = serializers.CharField(allow_blank=True)
    status_code = serializers.IntegerField(allow_null=True)
