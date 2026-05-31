"""Session-cookie auth (Phase 5 auth/login slice).

The FIRST suite that exercises real ``SessionAuthentication`` — a login POST sets
``sessionid`` and the SAME ``APIClient``'s cookie jar then authenticates a
follow-up request. Every other API test uses ``force_authenticate``, which
bypasses the session machinery entirely; this file does not.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

LOGIN_URL = reverse("core:auth-login")
LOGOUT_URL = reverse("core:auth-logout")
ME_URL = reverse("core:auth-me")
# A representative previously-403 endpoint, to prove the session actually authenticates.
GUARDED_URL = reverse("cards:card-list")

CREDS = {"username": "reader", "password": "correct-horse-battery"}


# --- login: credential validation -----------------------------------------------


@pytest.mark.django_db
def test_login_with_valid_credentials_establishes_session(user: User) -> None:
    """200 + an HttpOnly ``sessionid`` cookie, and the same client (cookie jar)
    can then reach a previously-403 endpoint via real SessionAuthentication."""
    client = APIClient()
    # baseline: anonymous is 403 on a guarded read endpoint
    assert client.get(GUARDED_URL).status_code == status.HTTP_403_FORBIDDEN

    resp = client.post(LOGIN_URL, CREDS, format="json")

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {"id": user.id, "username": "reader", "email": "r@example.com"}
    assert "sessionid" in resp.cookies
    # The session cookie MUST be HttpOnly (JS-unreadable) — the exact INVERSE of the
    # csrftoken cookie (test_health.py asserts that one is NOT HttpOnly). A
    # JS-readable session cookie is an XSS token-theft vector.
    assert resp.cookies["sessionid"]["httponly"]

    # The cookie jar now carries sessionid, so the follow-up GET is genuine cookie auth.
    assert client.get(GUARDED_URL).status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_login_with_wrong_password_is_rejected_400(user: User) -> None:
    client = APIClient()
    resp = client.post(LOGIN_URL, {"username": "reader", "password": "nope"}, format="json")

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    # No session established.
    assert client.get(ME_URL).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_login_with_unknown_user_is_rejected_400(user: User) -> None:
    """Same 400 + same generic message as a wrong password — no user enumeration."""
    client = APIClient()
    wrong_user = client.post(
        LOGIN_URL, {"username": "ghost", "password": "nope"}, format="json"
    )
    wrong_pw = client.post(
        LOGIN_URL, {"username": "reader", "password": "nope"}, format="json"
    )

    assert wrong_user.status_code == status.HTTP_400_BAD_REQUEST
    assert wrong_pw.status_code == status.HTTP_400_BAD_REQUEST
    assert wrong_user.json() == wrong_pw.json()


@pytest.mark.django_db
def test_login_with_missing_fields_is_rejected_400(user: User) -> None:
    client = APIClient()
    resp = client.post(LOGIN_URL, {"username": "reader"}, format="json")

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "password" in resp.json()


@pytest.mark.django_db
def test_login_with_inactive_user_is_rejected_400(user: User) -> None:
    user.is_active = False
    user.save(update_fields=["is_active"])
    client = APIClient()
    resp = client.post(LOGIN_URL, CREDS, format="json")

    assert resp.status_code == status.HTTP_400_BAD_REQUEST


def test_login_route_is_reachable_anonymously() -> None:
    """A GET (no handler → 405) proves the route is anonymously reachable — NOT a
    403. If login itself required auth, no one could ever sign in."""
    resp = APIClient().get(LOGIN_URL)

    assert resp.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


@pytest.mark.django_db
def test_login_is_rate_limited(user: User) -> None:
    """Login is the one anonymous credential surface, so it's throttled (5/min) —
    CSRF can't stop a direct brute-forcer that seeds its own token. Attempts are
    counted before the handler, so even bad-cred 400s burn the budget; the 6th is
    denied with 429. (Cache isolation via the autouse conftest fixture.)"""
    client = APIClient()
    bad = {"username": "reader", "password": "wrong"}

    statuses = [client.post(LOGIN_URL, bad, format="json").status_code for _ in range(6)]

    assert statuses[:5] == [status.HTTP_400_BAD_REQUEST] * 5
    assert statuses[5] == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.django_db
def test_login_rate_limit_ignores_spoofed_forwarded_for(user: User) -> None:
    """A client rotating X-Forwarded-For must NOT earn a fresh throttle bucket per
    request. NUM_PROXIES=0 makes DRF key on REMOTE_ADDR, not the spoofable header;
    without it DRF's default keys on the whole XFF and the 6th attempt below would
    still be 400 (the bypass) instead of 429."""
    client = APIClient()
    bad = {"username": "reader", "password": "wrong"}

    statuses = [
        client.post(
            LOGIN_URL, bad, format="json", HTTP_X_FORWARDED_FOR=f"203.0.113.{i}"
        ).status_code
        for i in range(6)
    ]

    assert statuses[:5] == [status.HTTP_400_BAD_REQUEST] * 5
    assert statuses[5] == status.HTTP_429_TOO_MANY_REQUESTS


# --- login: CSRF (csrf_protect re-arms despite DRF's csrf_exempt) ----------------


@pytest.mark.django_db
def test_login_without_csrf_token_is_rejected(user: User) -> None:
    """With CSRF enforcement on, a login POST lacking the token → 403. Proves
    ``@csrf_protect`` re-arms CSRF even though DRF marks the APIView csrf_exempt
    (and SessionAuthentication never runs enforce_csrf for an anonymous POST)."""
    enforced = APIClient(enforce_csrf_checks=True)
    resp = enforced.post(LOGIN_URL, CREDS, format="json")

    assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_login_with_seeded_csrf_token_succeeds(user: User) -> None:
    enforced = APIClient(enforce_csrf_checks=True)
    enforced.get(reverse("core:csrf"))  # seeds the csrftoken cookie
    token = enforced.cookies["csrftoken"].value

    resp = enforced.post(LOGIN_URL, CREDS, format="json", HTTP_X_CSRFTOKEN=token)

    assert resp.status_code == status.HTTP_200_OK


# --- logout ----------------------------------------------------------------------


@pytest.mark.django_db
def test_logout_clears_the_session(user: User) -> None:
    client = APIClient()
    client.post(LOGIN_URL, CREDS, format="json")
    assert client.get(GUARDED_URL).status_code == status.HTTP_200_OK  # authed

    out = client.post(LOGOUT_URL)

    assert out.status_code == status.HTTP_200_OK
    # Session gone → guarded endpoints 403 again.
    assert client.get(GUARDED_URL).status_code == status.HTTP_403_FORBIDDEN
    assert client.get(ME_URL).status_code == status.HTTP_403_FORBIDDEN


def test_logout_requires_authentication() -> None:
    assert APIClient().post(LOGOUT_URL).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_logout_enforces_csrf(user: User) -> None:
    """Logout is an authenticated POST, so SessionAuthentication.enforce_csrf gates
    it (no csrf_protect needed, unlike login) — the documented forced-logout
    protection. NOTE: ``login()`` rotates the CSRF token, so the valid token must
    be read AFTER login, not the one seeded before it."""
    enforced = APIClient(enforce_csrf_checks=True)
    enforced.get(reverse("core:csrf"))
    login_resp = enforced.post(
        LOGIN_URL, CREDS, format="json", HTTP_X_CSRFTOKEN=enforced.cookies["csrftoken"].value
    )
    assert login_resp.status_code == status.HTTP_200_OK
    rotated = enforced.cookies["csrftoken"].value

    # No CSRF header → rejected even though authenticated.
    assert enforced.post(LOGOUT_URL).status_code == status.HTTP_403_FORBIDDEN
    # With the post-login (rotated) token → succeeds.
    assert (
        enforced.post(LOGOUT_URL, HTTP_X_CSRFTOKEN=rotated).status_code
        == status.HTTP_200_OK
    )


# --- me --------------------------------------------------------------------------


@pytest.mark.django_db
def test_me_returns_current_user_when_authenticated(user: User) -> None:
    client = APIClient()
    client.post(LOGIN_URL, CREDS, format="json")

    resp = client.get(ME_URL)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {"id": user.id, "username": "reader", "email": "r@example.com"}


def test_me_requires_authentication() -> None:
    assert APIClient().get(ME_URL).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_user_payload_never_leaks_sensitive_fields(user: User) -> None:
    """Least-disclosure: no password hash, no staff/superuser flags, no permissions."""
    client = APIClient()
    client.post(LOGIN_URL, CREDS, format="json")

    body = client.get(ME_URL).json()

    assert set(body) == {"id", "username", "email"}
    for leaked in ("password", "is_staff", "is_superuser", "last_login", "user_permissions"):
        assert leaked not in body
