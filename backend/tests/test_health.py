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


def test_csrf_endpoint_seeds_cookie_anonymously() -> None:
    """The SPA seeds its CSRF token here before its first POST (slice 6). Must work
    without auth (a not-yet-signed-in browser seeds it) and actually set the cookie —
    nothing else in this all-JSON API calls get_token, so without this view the token
    is never minted. The cookie must be readable by JS/proxy.ts (not HttpOnly) so
    proxy.ts can copy it into X-CSRFToken."""
    client = APIClient()
    response = client.get("/api/csrf/")

    assert response.status_code == status.HTTP_200_OK
    assert "csrftoken" in response.cookies
    # Non-HttpOnly: the browser/proxy must read the value to echo it back.
    assert not response.cookies["csrftoken"]["httponly"]
