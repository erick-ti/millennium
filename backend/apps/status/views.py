from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.status.collectors import build_overview
from apps.status.providers.healthchecks import build_checks_status
from apps.status.serializers import ChecksStatusSerializer, StatusOverviewSerializer


class StatusOverviewView(APIView):
    """Status dashboard — the internal tier (the live pipeline flow + app state).

    Inherits the global ``IsAuthenticated``: the dashboard exposes operational internals
    (run history, catalog size, deployed commit), not public data. A single typed dict
    via ``@extend_schema`` (the ``HealthView`` shape), so the generated client gets a
    typed ``statusOverviewRetrieve``. Pure DB reads, NOT cached — the heart of the page
    must feel live; only the external provider tiers (Healthchecks/Hetzner) are cached."""

    @extend_schema(
        summary="Status dashboard — internal pipeline + app health",
        responses={200: StatusOverviewSerializer},
    )
    def get(self, request: Request) -> Response:
        return Response(StatusOverviewSerializer(build_overview()).data)


class ChecksStatusView(APIView):
    """Status dashboard — the Healthchecks tier (the flow's backup + CD dead-man nodes).

    Separate endpoint (not folded into overview) so a slow/down external provider
    degrades this tile alone and never blocks the live internal flow. The provider
    caches + fails closed to ``available: false`` (always 200, never 500). Optional:
    ``configured: false`` without a read-API key."""

    @extend_schema(
        summary="Status dashboard — Healthchecks backup + CD checks",
        responses={200: ChecksStatusSerializer},
    )
    def get(self, request: Request) -> Response:
        return Response(ChecksStatusSerializer(build_checks_status()).data)
