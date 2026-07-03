from __future__ import annotations

from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.audit.views import AuditEventViewSet, ClientErrorView, ErrorGroupViewSet

# Included at /api/audit/ (config/urls.py), WITHOUT a namespace so the resolved view_name
# for the beacon is the bare "client-error" that AuditMiddleware._SKIP_VIEW_NAMES checks.
router = DefaultRouter()
router.register("events", AuditEventViewSet, basename="audit-event")
router.register("error-groups", ErrorGroupViewSet, basename="audit-error-group")

urlpatterns = [
    path("client-errors/", ClientErrorView.as_view(), name="client-error"),
    *router.urls,
]
