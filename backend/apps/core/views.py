from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Liveness probe — returns 200 when the process is up."""

    permission_classes = [AllowAny]
    authentication_classes: list[type] = []

    @extend_schema(
        summary="Service health",
        responses={200: OpenApiResponse(description="Service is healthy")},
    )
    def get(self, request: Request) -> Response:
        return Response({"status": "ok"}, status=status.HTTP_200_OK)
