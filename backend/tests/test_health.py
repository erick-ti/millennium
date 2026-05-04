from rest_framework import status
from rest_framework.test import APIClient


def test_health_endpoint_returns_ok() -> None:
    client = APIClient()
    response = client.get("/api/health/")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


def test_schema_requires_authentication() -> None:
    """Schema reveals API shape — must not be anonymously readable."""
    client = APIClient()
    response = client.get("/api/schema/")

    assert response.status_code == status.HTTP_403_FORBIDDEN
