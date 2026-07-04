from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.core.management import call_command
from django.db.models import Count
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit import utils
from apps.audit.middleware import AuditMiddleware
from apps.audit.models import AuditEvent, ErrorLog
from apps.core.permissions import IsSuperUser

# ---------------------------------------------------------------------------
# Pure helpers (no DB)
# ---------------------------------------------------------------------------


def test_hash_session_key_is_deterministic_and_never_the_raw_key() -> None:
    assert utils.hash_session_key(None) == ""
    assert utils.hash_session_key("") == ""
    hashed = utils.hash_session_key("abc123session")
    assert hashed == utils.hash_session_key("abc123session")
    assert hashed != "abc123session"
    assert len(hashed) == 64


def test_normalize_path_collapses_numeric_ids() -> None:
    assert utils.normalize_path("/api/decks/1/") == "/api/decks/:id/"
    assert utils.normalize_path("/api/decks/42") == "/api/decks/:id"
    assert utils.normalize_path("/api/cards/cards/") == "/api/cards/cards/"


def test_truncate_marks_cut_text() -> None:
    assert utils.truncate("short", 10) == "short"
    cut = utils.truncate("x" * 100, 10)
    assert cut.startswith("x" * 10)
    assert "truncated" in cut


def test_fingerprint_groups_same_error_across_ids_but_splits_distinct_ones() -> None:
    fp1 = utils.fingerprint_error(
        source="backend", exception_class="ValueError", path="/api/x/1/", signature="boom"
    )
    fp2 = utils.fingerprint_error(
        source="backend", exception_class="ValueError", path="/api/x/2/", signature="boom"
    )
    fp3 = utils.fingerprint_error(
        source="backend", exception_class="KeyError", path="/api/x/1/", signature="boom"
    )
    assert fp1 == fp2  # numeric id normalized away
    assert fp1 != fp3  # different exception class


def test_classify_actor() -> None:
    assert utils.classify_actor(AnonymousUser()) == "anonymous"

    owner = User(username="owner")
    owner.set_password("pw")
    assert utils.classify_actor(owner) == "user"

    demo = User(username="demo")
    demo.set_unusable_password()
    assert utils.classify_actor(demo) == "demo"


def test_is_superuser_permission_gate() -> None:
    factory = RequestFactory()
    perm = IsSuperUser()
    request = factory.get("/api/audit/events/")

    request.user = AnonymousUser()
    assert perm.has_permission(request, None) is False  # type: ignore[arg-type]

    request.user = User(username="u", is_superuser=False)
    assert perm.has_permission(request, None) is False  # type: ignore[arg-type]

    request.user = User(username="owner", is_superuser=True)
    assert perm.has_permission(request, None) is True  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Middleware behaviour (DB)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_unsafe_request_records_one_audit_event() -> None:
    # A real (non-demo) account's write is recorded — even a 4xx (the owner being denied is a
    # genuine, bounded signal; only PUBLIC denials are dropped).
    owner = User.objects.create_user("owner", password="pw-12345!")
    client = APIClient()
    client.force_login(owner)
    resp = client.post("/api/alerts/rules/", {"bad": "data"}, format="json")
    assert resp.status_code == 400  # validation error, still audited for a real account

    event = AuditEvent.objects.get()
    assert event.method == "POST"
    assert event.path == "/api/alerts/rules/"
    assert event.status_code == 400
    assert event.actor_type == "user"
    assert event.duration_ms is not None
    # Correlation id from django_structlog's RequestMiddleware (proves the ordering works).
    assert event.request_id != ""


@pytest.mark.django_db
def test_demo_actions_are_not_audited() -> None:
    # The read-only demo is a PUBLIC showcase: demo-login is a 30/min public growth vector and
    # no demo action mutates data, so the DEMO actor is skipped entirely — repeated demo-logins
    # (and demo logout) grow the audit table by zero.
    from django.core.cache import cache

    cache.clear()  # deterministic demo_login throttle bucket
    demo = User.objects.create(username="demo")
    demo.set_unusable_password()
    demo.save()

    client = APIClient()
    for _ in range(3):
        assert client.post("/api/auth/demo-login/", format="json").status_code == 200
    assert client.post("/api/auth/logout/").status_code == 200  # demo logout

    assert AuditEvent.objects.count() == 0


@pytest.mark.django_db
def test_owner_login_success_is_audited() -> None:
    # A public request that RESOLVES to a real account IS audited — the pre/post identity picks
    # the authenticated side, so an owner login (anonymous → user) records as a real action.
    User.objects.create_user("owner", password="pw-12345!")

    client = APIClient()
    resp = client.post(
        "/api/auth/login/",
        {"username": "owner", "password": "pw-12345!"},
        format="json",
    )
    assert resp.status_code == 200

    event = AuditEvent.objects.get(path="/api/auth/login/")
    assert event.actor_type == "user"
    assert event.status_code == 200


@pytest.mark.django_db
def test_get_request_is_not_audited() -> None:
    client = APIClient()
    resp = client.get("/api/health/")
    assert resp.status_code == 200
    assert AuditEvent.objects.count() == 0


@pytest.mark.django_db
def test_logout_is_attributed_to_the_real_actor() -> None:
    # logout() resets request.user to AnonymousUser mid-request; the pre-view snapshot keeps
    # the real actor + pre-flush session, so the trail isn't a phantom anonymous logout.
    owner = User.objects.create_user("owner", password="pw-12345!")
    client = APIClient()
    client.force_login(owner)
    resp = client.post("/api/auth/logout/")
    assert resp.status_code == 200

    event = AuditEvent.objects.get(path="/api/auth/logout/")
    assert event.actor_type == "user"
    assert event.actor == owner
    assert event.session_key_hash  # the pre-flush session, not the emptied post-flush one


@pytest.mark.django_db
def test_failed_login_is_audited() -> None:
    # A failed login is a bounded (throttled 5/min), security-relevant signal, so it is
    # audited even though it's a public 4xx (the login carve-out).
    client = APIClient()
    resp = client.post(
        "/api/auth/login/", {"username": "nobody", "password": "wrong"}, format="json"
    )
    assert resp.status_code == 400

    event = AuditEvent.objects.get(path="/api/auth/login/")
    assert event.actor_type == "anonymous"
    assert event.status_code == 400


@pytest.mark.django_db
def test_public_denied_writes_are_not_audited() -> None:
    # A public (anonymous or demo) unsafe request that is denied (4xx) is a no-op probe on an
    # UNTHROTTLED endpoint — recording it would let a public loop grow the 365-day audit
    # table and bury the real trail. Neither the anonymous nor the demo 403 is audited.
    anon_client = APIClient()
    assert (
        anon_client.post("/api/alerts/rules/", {"bad": "data"}, format="json").status_code
        == 403
    )

    demo = User.objects.create(username="demo")
    demo.set_unusable_password()
    demo.save()
    demo_client = APIClient()
    demo_client.force_login(demo)
    assert (
        demo_client.post(
            "/api/alerts/rules/",
            {"threshold_pct": "10", "window_days": 7, "direction": "any"},
            format="json",
        ).status_code
        == 403
    )

    assert AuditEvent.objects.count() == 0


@pytest.mark.django_db
def test_public_write_spam_does_not_grow_the_audit_table() -> None:
    # The abuse bound: repeated anonymous POSTs to an unthrottled protected endpoint must
    # not accumulate audit rows.
    client = APIClient()
    for _ in range(5):
        assert (
            client.post("/api/decks/decks/", {"name": "x"}, format="json").status_code
            == 403
        )
    assert AuditEvent.objects.count() == 0


@pytest.mark.django_db
def test_session_key_is_hashed_never_stored_raw() -> None:
    owner = User.objects.create_user("owner", password="s3cret-pw!!")
    client = APIClient()
    client.force_login(owner)
    raw_key = client.session.session_key
    assert raw_key

    client.post("/api/alerts/rules/", {"missing": "fields"}, format="json")

    event = AuditEvent.objects.filter(actor=owner).latest("created_at")
    assert event.actor_type == "user"
    assert len(event.session_key_hash) == 64
    assert event.session_key_hash == utils.hash_session_key(raw_key)
    assert raw_key not in event.session_key_hash


@pytest.mark.django_db
def test_process_exception_records_error_log_with_traceback() -> None:
    factory = RequestFactory()
    request = factory.post("/api/decks/1/")
    request.user = AnonymousUser()
    middleware = AuditMiddleware(lambda r: HttpResponse())

    try:
        raise ValueError("boom happened")
    except ValueError as exc:
        middleware.process_exception(request, exc)

    log = ErrorLog.objects.get()
    assert log.source == "backend"
    assert log.exception_class == "ValueError"
    assert "boom happened" in log.message
    assert log.traceback
    assert log.fingerprint
    assert log.status_code == 500
    assert getattr(request, "_audit_error_recorded", False) is True


@pytest.mark.django_db
def test_process_exception_skips_expected_control_flow() -> None:
    # Http404 / PermissionDenied / SuspiciousOperation (incl. RequestDataTooBig) are Django
    # control flow rendered as 4xx, not errors — they must NOT become phantom 500 ErrorLogs
    # (RequestDataTooBig would additionally let an oversized beacon dodge the frontend quota).
    from django.core.exceptions import (
        PermissionDenied,
        RequestDataTooBig,
        SuspiciousOperation,
    )
    from django.http import Http404

    factory = RequestFactory()
    request = factory.post("/admin/something/")
    request.user = AnonymousUser()
    middleware = AuditMiddleware(lambda r: HttpResponse())

    middleware.process_exception(request, Http404("missing"))
    middleware.process_exception(request, PermissionDenied("nope"))
    middleware.process_exception(request, SuspiciousOperation("bad host"))
    middleware.process_exception(request, RequestDataTooBig("too big"))

    assert ErrorLog.objects.count() == 0


@pytest.mark.django_db
def test_five_hundred_response_records_error_log_once() -> None:
    factory = RequestFactory()
    request = factory.get("/api/boom/")
    request.user = AnonymousUser()
    middleware = AuditMiddleware(lambda r: HttpResponse(status=500))

    response = middleware(request)

    assert response.status_code == 500
    assert ErrorLog.objects.count() == 1  # not double-recorded
    log = ErrorLog.objects.get()
    assert log.status_code == 500
    assert log.exception_class == ""
    # A bare 500 response is not an unsafe-method write, so no AuditEvent.
    assert AuditEvent.objects.count() == 0


@pytest.mark.django_db
def test_errors_group_by_fingerprint() -> None:
    for _ in range(3):
        ErrorLog.objects.create(
            source="backend", fingerprint="dup", exception_class="ValueError", status_code=500
        )
    ErrorLog.objects.create(source="backend", fingerprint="other", status_code=500)

    counts = {
        row["fingerprint"]: row["n"]
        for row in ErrorLog.objects.values("fingerprint").annotate(n=Count("id"))
    }
    assert counts["dup"] == 3
    assert counts["other"] == 1


# ---------------------------------------------------------------------------
# /ops read API (superuser only)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_ops_audit_feed_is_superuser_only() -> None:
    AuditEvent.objects.create(method="POST", path="/x", status_code=201, actor_type="user")

    # Anonymous → 403
    assert APIClient().get("/api/audit/events/").status_code == 403

    # Demo → 403
    demo = User.objects.create(username="demo")
    demo.set_unusable_password()
    demo.save()
    demo_client = APIClient()
    demo_client.force_login(demo)
    assert demo_client.get("/api/audit/events/").status_code == 403

    # Authenticated non-superuser → 403
    plain = User.objects.create_user("plain", password="pw-12345!")
    plain_client = APIClient()
    plain_client.force_login(plain)
    assert plain_client.get("/api/audit/events/").status_code == 403

    # Superuser → 200
    owner = User.objects.create_superuser("owner", "o@example.com", "pw-12345!")
    owner_client = APIClient()
    owner_client.force_login(owner)
    resp = owner_client.get("/api/audit/events/")
    assert resp.status_code == 200
    assert resp.json()["count"] >= 1


@pytest.mark.django_db
def test_ops_audit_feed_filters_by_actor_type() -> None:
    AuditEvent.objects.create(method="POST", path="/a", status_code=200, actor_type="user")
    AuditEvent.objects.create(method="POST", path="/b", status_code=403, actor_type="demo")

    owner = User.objects.create_superuser("owner", "o@example.com", "pw-12345!")
    client = APIClient()
    client.force_login(owner)

    resp = client.get("/api/audit/events/?actor_type=demo")
    assert resp.status_code == 200
    assert {row["path"] for row in resp.json()["results"]} == {"/b"}


@pytest.mark.django_db
def test_ops_error_groups_aggregate_and_filter() -> None:
    for _ in range(3):
        ErrorLog.objects.create(
            source="backend", fingerprint="dup", exception_class="ValueError",
            message="m1", status_code=500,
        )
    ErrorLog.objects.create(
        source="frontend", fingerprint="solo", exception_class="TypeError", message="m2"
    )

    owner = User.objects.create_superuser("owner", "o@example.com", "pw-12345!")
    client = APIClient()
    client.force_login(owner)

    resp = client.get("/api/audit/error-groups/")
    assert resp.status_code == 200
    groups = {g["fingerprint"]: g for g in resp.json()["results"]}
    assert groups["dup"]["count"] == 3
    assert groups["dup"]["source"] == "backend"
    assert groups["dup"]["message"] == "m1"
    assert groups["solo"]["count"] == 1

    filtered = client.get("/api/audit/error-groups/?source=frontend")
    assert {g["fingerprint"] for g in filtered.json()["results"]} == {"solo"}


@pytest.mark.django_db
def test_ops_error_groups_is_superuser_only() -> None:
    ErrorLog.objects.create(source="backend", fingerprint="f", status_code=500)
    assert APIClient().get("/api/audit/error-groups/").status_code == 403


# ---------------------------------------------------------------------------
# Retention pruning
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Frontend error beacon (POST /api/audit/client-errors/)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_client_error_beacon_records_frontend_errorlog() -> None:
    client = APIClient()
    resp = client.post(
        "/api/audit/client-errors/",
        {
            "message": "TypeError: cannot read x",
            "name": "TypeError",
            "stack": "at Foo (app.js:1)",
            "url": "/collection?token=secret",
            "request_id": "req-abc-123",
        },
        format="json",
    )
    assert resp.status_code == 204

    log = ErrorLog.objects.get()
    assert log.source == "frontend"
    assert log.exception_class == "TypeError"
    assert "cannot read x" in log.message
    assert log.traceback == "at Foo (app.js:1)"
    # Query string stripped — never store a token passed as ?param.
    assert log.path == "/collection"
    # The failed call's request id is preserved for correlation.
    assert log.request_id == "req-abc-123"


@pytest.mark.django_db
def test_client_error_beacon_is_not_itself_audited() -> None:
    client = APIClient()
    client.post("/api/audit/client-errors/", {"message": "boom"}, format="json")
    # The beacon is a POST but must be skipped by the audit middleware (else every error
    # report floods the audit trail). ErrorLog captures it; AuditEvent must not.
    assert ErrorLog.objects.count() == 1
    assert AuditEvent.objects.count() == 0


@pytest.mark.django_db
def test_client_error_requires_message() -> None:
    client = APIClient()
    resp = client.post("/api/audit/client-errors/", {"name": "X"}, format="json")
    assert resp.status_code == 400
    assert ErrorLog.objects.count() == 0


@pytest.mark.django_db
def test_client_error_rejects_oversized_payload() -> None:
    client = APIClient()
    resp = client.post(
        "/api/audit/client-errors/", {"message": "x" * 20_000}, format="json"
    )
    assert resp.status_code == 413
    assert ErrorLog.objects.count() == 0


@pytest.mark.django_db
def test_oversized_beacon_returns_413_and_records_no_errorlog(settings) -> None:  # type: ignore[no-untyped-def]
    # An oversized body must not slip past the frontend quota by becoming a backend 500:
    # the view returns 413 (catching RequestDataTooBig) and the middleware skips the
    # SuspiciousOperation, so NO ErrorLog of any source is recorded.
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 100
    client = APIClient()
    resp = client.post(
        "/api/audit/client-errors/", {"message": "x" * 500}, format="json"
    )
    assert resp.status_code == 413
    assert ErrorLog.objects.count() == 0


@pytest.mark.django_db
def test_client_error_requires_csrf() -> None:
    # The regression the review asked for: AllowAny does NOT mean CSRF-naked. With CSRF
    # enforcement on and no token, the beacon must be rejected, never recorded.
    client = APIClient(enforce_csrf_checks=True)
    resp = client.post("/api/audit/client-errors/", {"message": "boom"}, format="json")
    assert resp.status_code == 403
    assert ErrorLog.objects.count() == 0


def test_validated_retention_days_rejects_non_positive() -> None:
    # A bad env value (0 or negative) makes the prune cutoff now-or-future and would wipe the
    # append-only store — it must fail closed at boot.
    from django.core.exceptions import ImproperlyConfigured

    from config.settings.base import _validated_retention_days

    assert _validated_retention_days("X", 30) == 30
    with pytest.raises(ImproperlyConfigured):
        _validated_retention_days("X", 0)
    with pytest.raises(ImproperlyConfigured):
        _validated_retention_days("X", -1)


@pytest.mark.django_db
def test_prune_audit_respects_retention_and_frontend_split(settings) -> None:  # type: ignore[no-untyped-def]
    settings.AUDIT_EVENT_RETENTION_DAYS = 30
    settings.ERROR_LOG_RETENTION_DAYS = 90
    settings.FRONTEND_ERROR_LOG_RETENTION_DAYS = 10

    old_event = AuditEvent.objects.create(method="POST", path="/x", status_code=200)
    new_event = AuditEvent.objects.create(method="POST", path="/y", status_code=200)
    AuditEvent.objects.filter(pk=old_event.pk).update(
        created_at=timezone.now() - timedelta(days=40)
    )

    # Both errors aged 20 days: the frontend one is past its 10-day window (pruned), the
    # backend one is well within 90 days (kept) — the public-beacon split.
    frontend_error = ErrorLog.objects.create(source="frontend", fingerprint="fe")
    backend_error = ErrorLog.objects.create(
        source="backend", fingerprint="be", status_code=500
    )
    ErrorLog.objects.filter(pk__in=[frontend_error.pk, backend_error.pk]).update(
        created_at=timezone.now() - timedelta(days=20)
    )

    call_command("prune_audit")

    assert not AuditEvent.objects.filter(pk=old_event.pk).exists()
    assert AuditEvent.objects.filter(pk=new_event.pk).exists()
    assert not ErrorLog.objects.filter(pk=frontend_error.pk).exists()
    assert ErrorLog.objects.filter(pk=backend_error.pk).exists()


@pytest.mark.django_db
def test_public_frontend_beacon_quota_covers_anonymous_and_demo(settings) -> None:  # type: ignore[no-untyped-def]
    # The public write bound must treat the demo as public — the demo session is obtainable
    # in one click (AllowAny demo-login), so it must NOT bypass the quota.
    settings.MAX_PUBLIC_FRONTEND_ERRORS_PER_DAY = 2

    demo = User.objects.create(username="demo")
    demo.set_unusable_password()
    demo.save()
    demo_client = APIClient()
    demo_client.force_login(demo)

    for _ in range(2):
        assert (
            demo_client.post(
                "/api/audit/client-errors/", {"message": "boom"}, format="json"
            ).status_code
            == 204
        )
    # The public bucket is now spent — a third DEMO beacon is dropped (still 204, no row).
    assert (
        demo_client.post(
            "/api/audit/client-errors/", {"message": "boom"}, format="json"
        ).status_code
        == 204
    )
    assert ErrorLog.objects.filter(source="frontend", actor_type="demo").count() == 2

    # Anonymous shares the same public bucket, so it is dropped too (no bypass either way).
    anon_client = APIClient()
    assert (
        anon_client.post(
            "/api/audit/client-errors/", {"message": "boom"}, format="json"
        ).status_code
        == 204
    )
    assert ErrorLog.objects.filter(source="frontend", actor_type="anonymous").count() == 0

    # A REAL (non-demo) account is exempt — the owner's reports are always recorded.
    owner = User.objects.create_user("owner", password="pw-12345!")
    owner_client = APIClient()
    owner_client.force_login(owner)
    assert (
        owner_client.post(
            "/api/audit/client-errors/", {"message": "boom"}, format="json"
        ).status_code
        == 204
    )
    assert ErrorLog.objects.filter(source="frontend", actor_type="user").count() == 1
