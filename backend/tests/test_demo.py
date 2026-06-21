"""Read-only demo account (recruiter showcase, demo-access branch).

A public ``POST /api/auth/demo-login/`` ``login()``s the seeded ``demo`` user; the
global ``DemoReadOnly`` permission then denies that session every unsafe method, so a
recruiter browses the owner's real data but can never mutate it. Logout is the one write
the demo may perform (so it's never trapped in the session). ``DemoReadOnly`` is a no-op
for every real account, and the schema/docs stay closed to the demo (Invariant 7).
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.core.permissions import DEMO_USERNAME

DEMO_LOGIN_URL = reverse("core:auth-demo-login")
LOGIN_URL = reverse("core:auth-login")
LOGOUT_URL = reverse("core:auth-logout")
ME_URL = reverse("core:auth-me")
CSRF_URL = reverse("core:csrf")
# Matches the conftest `user` fixture (username "reader").
OWNER_CREDS = {"username": "reader", "password": "correct-horse-battery"}
# A representative previously-403 read endpoint, to prove the demo session authenticates.
GUARDED_READ_URL = reverse("cards:card-list")

# The write surface the demo account must NOT reach (one representative per write app).
WRITE_URLS = [
    reverse("decks:deck-list"),  # create a deck (POST)
    reverse("decks:deckmembership-list"),  # add a holding to a deck (POST)
    reverse("alerts:alertrule-list"),  # create an alert rule (POST)
    reverse("imports:importbatch-list"),  # upload a CSV (POST)
    # A detail @action POST — DemoReadOnly checks has_permission BEFORE get_object, so
    # the row need not exist (a real account would 404; the demo is blocked at 403 first).
    reverse("imports:importrow-approve", kwargs={"pk": 1}),
]


@pytest.fixture
def demo_user(db: None) -> User:
    """The seeded read-only demo account, as ``ensure_demo_user`` creates it: a plain
    ``create_user`` with no password yields an unusable password (entered only via
    demo-login), unprivileged and active."""
    return User.objects.create_user(username=DEMO_USERNAME)


def _demo_client(demo_user: User) -> APIClient:
    client = APIClient()
    assert client.post(DEMO_LOGIN_URL).status_code == status.HTTP_200_OK
    return client


# --- demo-login ------------------------------------------------------------------


@pytest.mark.django_db
def test_demo_login_establishes_read_only_session(demo_user: User) -> None:
    client = APIClient()
    assert client.get(GUARDED_READ_URL).status_code == status.HTTP_403_FORBIDDEN

    resp = client.post(DEMO_LOGIN_URL)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "id": demo_user.id,
        "username": DEMO_USERNAME,
        "email": "",
        "is_demo": True,
    }
    assert "sessionid" in resp.cookies
    # The session now authenticates a previously-403 read endpoint via real SessionAuth.
    assert client.get(GUARDED_READ_URL).status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_demo_login_without_seeded_account_returns_404() -> None:
    """No demo account (dev/test, or before ensure_demo_user runs) → a graceful 404,
    not a 500, so the SPA can hide the CTA / fall back."""
    assert APIClient().post(DEMO_LOGIN_URL).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_demo_login_refuses_an_account_with_a_usable_password() -> None:
    """Provenance guard (the seed_smoke precedent): demo-login only mints a session for
    the seed-owned, password-less account. A hand-created 'demo' that has a usable
    password (e.g. a forgotten privileged account) is NOT enterable via this public
    endpoint → 404; it must sign in normally."""
    User.objects.create_user(username=DEMO_USERNAME, password="hand-created-pw")

    assert APIClient().post(DEMO_LOGIN_URL).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
@pytest.mark.parametrize("privilege", ["is_staff", "is_superuser"])
def test_demo_login_refuses_a_privileged_account(privilege: str) -> None:
    """Full-posture provenance guard: even a PASSWORD-LESS 'demo' that drifted to
    staff/superuser is refused → 404. /admin/ is mounted outside DRF and shares the
    session cookie, so demo-login must never mint a privileged session — the API-level
    DemoReadOnly/IsNotDemoUser (which key on the username) would not protect /admin/."""
    user = User.objects.create_user(username=DEMO_USERNAME)  # no password → unusable
    setattr(user, privilege, True)
    user.save(update_fields=[privilege])
    assert not user.has_usable_password()  # only the privilege should trip the guard

    assert APIClient().post(DEMO_LOGIN_URL).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_demo_login_does_not_overwrite_an_owner_session(
    user: User, demo_user: User
) -> None:
    """An owner already in session who hits demo-login (e.g. a transient /me blip wrongly
    showed the CTA) is NOT downgraded — the endpoint returns the owner and leaves the
    session intact, never replacing it with the read-only demo."""
    client = APIClient()
    client.post(LOGIN_URL, OWNER_CREDS, format="json")  # establish the owner session
    assert client.get(ME_URL).json()["username"] == "reader"

    resp = client.post(DEMO_LOGIN_URL)

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["username"] == "reader"  # the owner, NOT "demo"
    assert body["is_demo"] is False
    # The session is still the owner's — a follow-up still authenticates as the owner.
    assert client.get(ME_URL).json()["username"] == "reader"


@pytest.mark.django_db
def test_demo_login_is_rate_limited(demo_user: User) -> None:
    """The demo_login scope enforces its OWN cap (30/min) returning 429 — guards against
    session-creation spam (one DB session row per call). A future drop of the scope from
    DEFAULT_THROTTLE_RATES would silently disable this (a missing scope → no rate), which
    this test catches. (Cache isolated by the autouse conftest fixture.)"""
    client = APIClient()
    statuses = [client.post(DEMO_LOGIN_URL).status_code for _ in range(31)]

    assert statuses[:30] == [status.HTTP_200_OK] * 30
    assert statuses[30] == status.HTTP_429_TOO_MANY_REQUESTS


def test_demo_login_route_is_reachable_anonymously() -> None:
    """A GET (no handler → 405) proves the route is anonymously reachable — not a 403."""
    assert (
        APIClient().get(DEMO_LOGIN_URL).status_code
        == status.HTTP_405_METHOD_NOT_ALLOWED
    )


@pytest.mark.django_db
def test_demo_login_uses_a_separate_throttle_bucket(demo_user: User) -> None:
    """Exhausting the login (credential) bucket must NOT throttle demo-login — they are
    distinct scopes, so demo traffic can't drain the brute-force speed bump."""
    client = APIClient()
    bad = {"username": "nobody", "password": "wrong"}
    statuses = [client.post(LOGIN_URL, bad, format="json").status_code for _ in range(6)]
    assert statuses[5] == status.HTTP_429_TOO_MANY_REQUESTS  # login bucket spent

    # demo-login is a different scope, same client/IP → still allowed.
    assert client.post(DEMO_LOGIN_URL).status_code == status.HTTP_200_OK


# --- the read-only block ---------------------------------------------------------


@pytest.mark.django_db
def test_demo_session_can_read(demo_user: User) -> None:
    client = _demo_client(demo_user)
    assert client.get(GUARDED_READ_URL).status_code == status.HTTP_200_OK
    assert client.get(ME_URL).status_code == status.HTTP_200_OK
    # The /status dashboard is part of the showcase — readable by the demo session.
    assert client.get("/api/status/overview/").status_code == status.HTTP_200_OK


@pytest.mark.django_db
@pytest.mark.parametrize("url", WRITE_URLS)
def test_demo_session_cannot_write(demo_user: User, url: str) -> None:
    """Every unsafe method is blocked by DemoReadOnly (403), checked BEFORE the view
    body — so even the @action POST and a non-existent row id are blocked at the
    permission layer, never reaching serializer validation or get_object()."""
    client = _demo_client(demo_user)

    assert client.post(url).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_demo_session_cannot_use_other_unsafe_methods(demo_user: User) -> None:
    """PUT/PATCH/DELETE are blocked too — DemoReadOnly is method-based, not action-based."""
    client = _demo_client(demo_user)
    deck_detail = reverse("decks:deck-detail", kwargs={"pk": 1})

    assert client.delete(deck_detail).status_code == status.HTTP_403_FORBIDDEN
    assert (
        client.patch(deck_detail, {"name": "x"}, format="json").status_code
        == status.HTTP_403_FORBIDDEN
    )


@pytest.mark.django_db
def test_demo_session_can_log_out(demo_user: User) -> None:
    """Logout is the one write the demo may perform — LogoutView opts out of
    DemoReadOnly so a 403 never strands the recruiter in the demo session."""
    client = _demo_client(demo_user)

    out = client.post(LOGOUT_URL)

    assert out.status_code == status.HTTP_200_OK
    assert client.get(GUARDED_READ_URL).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_demo_session_cannot_read_openapi_schema(demo_user: User) -> None:
    """Invariant 7 holds even for the authenticated demo: the schema is recon material,
    so SERVE_PERMISSIONS (IsNotDemoUser) keeps the demo out — anonymous already 403s
    (test_health.py), and a real owner still gets it (below)."""
    client = _demo_client(demo_user)

    assert client.get("/api/schema/").status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_demo_options_exposes_no_serializer_field_metadata(demo_user: User) -> None:
    """The OPTIONS metadata path respects the same recon boundary as the schema. OPTIONS is a
    safe method (allowed for the demo), but DRF's determine_actions gates the field-level
    ``actions`` block behind a per-method permission check that DemoReadOnly denies — so a demo
    OPTIONS on a write-capable endpoint returns NO serializer-field schema, only name/
    description/content-types (Codex review 2026-06-21: confirmed not a leak)."""
    client = _demo_client(demo_user)

    body = client.options(reverse("decks:deck-list")).json()

    assert "actions" not in body


# --- regression: a real account is unaffected ------------------------------------


@pytest.mark.django_db
def test_real_user_can_still_write(user: User) -> None:
    """DemoReadOnly is a no-op for a normal account — the owner can still create.
    (force_authenticate suffices: DemoReadOnly keys on the username, not the session.)"""
    client = APIClient()
    client.force_authenticate(user=user)

    resp = client.post(reverse("decks:deck-list"), {"name": "My Deck"}, format="json")

    assert resp.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
def test_a_passworded_demo_named_account_is_not_read_only_locked() -> None:
    """Identity is keyed on the password-less seed posture, not the username alone: a real
    account that reuses the reserved 'demo' name keeps its password, so is_demo_user is
    False and it has full write access — never silently downgraded to read-only (Codex
    review 2026-06-21)."""
    real = User.objects.create_user(username=DEMO_USERNAME, password="real-pw")
    client = APIClient()
    client.force_authenticate(user=real)

    resp = client.post(reverse("decks:deck-list"), {"name": "Real Deck"}, format="json")

    assert resp.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
def test_owner_can_write_through_a_real_session(user: User) -> None:
    """The owner's write completes through the GENUINE cookie + CSRF path with the full
    [IsAuthenticated, DemoReadOnly] chain — not just force_authenticate (which bypasses
    SessionAuthentication + CSRF). Proves DemoReadOnly is transparent on the real owner
    write path."""
    client = APIClient(enforce_csrf_checks=True)
    client.get(CSRF_URL)  # seed csrftoken
    login_resp = client.post(
        LOGIN_URL,
        OWNER_CREDS,
        format="json",
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )
    assert login_resp.status_code == status.HTTP_200_OK
    # login() rotates the CSRF token — read the post-login value.
    rotated = client.cookies["csrftoken"].value

    resp = client.post(
        reverse("decks:deck-list"),
        {"name": "Real Session Deck"},
        format="json",
        HTTP_X_CSRFTOKEN=rotated,
    )

    assert resp.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
def test_real_user_can_read_openapi_schema(user: User) -> None:
    """SERVE_PERMISSIONS still serves the real owner (IsNotDemoUser requires auth but
    excludes only the demo) — so the offline schema snapshot workflow is unaffected."""
    client = APIClient()
    client.force_authenticate(user=user)

    assert client.get("/api/schema/").status_code == status.HTTP_200_OK


# --- ensure_demo_user command ----------------------------------------------------


@pytest.mark.django_db
def test_ensure_demo_user_creates_unprivileged_passwordless_account() -> None:
    call_command("ensure_demo_user")

    demo = User.objects.get(username=DEMO_USERNAME)
    assert demo.is_active
    assert not demo.is_staff
    assert not demo.is_superuser
    assert not demo.has_usable_password()


@pytest.mark.django_db
def test_ensure_demo_user_is_idempotent() -> None:
    call_command("ensure_demo_user")
    call_command("ensure_demo_user")

    assert User.objects.filter(username=DEMO_USERNAME).count() == 1


@pytest.mark.django_db
def test_ensure_demo_user_does_not_clobber_an_existing_account() -> None:
    """Create-only by default: an existing 'demo' that isn't in the demo posture (here a
    privileged, passworded account) is LEFT UNTOUCHED on a plain run — never silently
    de-privileged/rewritten on deploy (Codex review 2026-06-21). demo-login still refuses
    it (the privileged/passworded 404 guard), so it's 'unavailable', not a hole."""
    User.objects.create_user(
        username=DEMO_USERNAME, password="real-pw", is_staff=True, is_superuser=True
    )

    call_command("ensure_demo_user")

    demo = User.objects.get(username=DEMO_USERNAME)
    assert demo.is_staff  # all unchanged
    assert demo.is_superuser
    assert demo.has_usable_password()


@pytest.mark.django_db
def test_ensure_demo_user_respects_a_deliberate_disable() -> None:
    """Kill switch: a demo account the owner disabled stays disabled across a re-run — the
    deploy seeder must not silently re-enable it."""
    user = User.objects.create_user(username=DEMO_USERNAME)
    user.is_active = False
    user.save(update_fields=["is_active"])

    call_command("ensure_demo_user")

    assert User.objects.get(username=DEMO_USERNAME).is_active is False


@pytest.mark.django_db
def test_ensure_demo_user_repair_flag_forces_the_posture() -> None:
    """--repair is the explicit opt-in to reclaim an existing account: it restores the
    unprivileged, password-less, active demo posture."""
    User.objects.create_user(
        username=DEMO_USERNAME, password="oops-usable", is_staff=True, is_superuser=True
    )

    call_command("ensure_demo_user", repair=True)

    demo = User.objects.get(username=DEMO_USERNAME)
    assert demo.is_active
    assert not demo.is_staff
    assert not demo.is_superuser
    assert not demo.has_usable_password()
