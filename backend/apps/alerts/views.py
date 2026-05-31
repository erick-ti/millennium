from __future__ import annotations

from django.db.models import QuerySet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError

from apps.alerts.models import AlertEvent, AlertRule
from apps.alerts.serializers import AlertEventSerializer, AlertRuleSerializer


@extend_schema_view(
    list=extend_schema(
        summary="The price-alert feed (newest first)",
        parameters=[
            OpenApiParameter(
                "rule",
                OpenApiTypes.INT,
                description="Filter the feed to one rule's events.",
            ),
        ],
    ),
)
class AlertEventViewSet(mixins.ListModelMixin, viewsets.GenericViewSet[AlertEvent]):
    """The in-app alert feed: append-only ``AlertEvent`` rows recorded by the daily
    evaluation, newest first. LIST-ONLY (a feed, like ``MoversViewSet`` — there is no
    per-event detail view, so no retrieve endpoint is exposed) and read-only (events are
    written only by ``run_alerts``). Inherits ``IsAuthenticated`` + ``PageNumberPagination``."""

    serializer_class = AlertEventSerializer

    def get_queryset(self) -> QuerySet[AlertEvent]:
        qs = AlertEvent.objects.select_related(
            "rule", "printing", "printing__card"
        ).order_by("-triggered_on", "-id")
        rule = self.request.query_params.get("rule")
        if rule is not None:
            if not rule.isdigit():
                raise ValidationError({"rule": "must be an integer rule id"})
            qs = qs.filter(rule_id=int(rule))
        return qs


@extend_schema_view(
    list=extend_schema(summary="List price-alert rules"),
    create=extend_schema(
        responses={201: AlertRuleSerializer, 400: OpenApiTypes.OBJECT},
        summary="Create a price-alert rule",
    ),
)
class AlertRuleViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet[AlertRule]
):
    """List + create price-alert rules. Create is the minimal write surface for this slice
    (edit/delete/mute UI deferred); the global CSRF + session auth apply (``proxy.ts``
    injects ``X-CSRFToken``). Inherits ``IsAuthenticated`` + ``PageNumberPagination``."""

    serializer_class = AlertRuleSerializer

    def get_queryset(self) -> QuerySet[AlertRule]:
        # Explicit (name, id) order: name isn't unique, so id is the deterministic
        # paginator tiebreaker (the read-API determinism convention).
        return AlertRule.objects.order_by("name", "id")
