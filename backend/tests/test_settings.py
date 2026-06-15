"""Regression tests for cross-environment settings safety."""

import importlib
import inspect
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory, override_settings

from config.settings import base, dev

# prod.py cannot be imported in tests: it reads SECRET_KEY/ALLOWED_HOSTS/etc.
# without defaults (Invariant 2's fail-closed posture), so module load itself
# raises ImproperlyConfigured. Read its source from disk instead.
_PROD_SOURCE = (
    Path(__file__).resolve().parents[1] / "config" / "settings" / "prod.py"
).read_text()


def test_base_settings_does_not_load_dotenv() -> None:
    """base.py must NOT load .env so prod settings fail closed even when a
    dev .env file exists at the repo root. dotenv loading belongs in dev.py.

    Without this guarantee, a developer accidentally running
    `DJANGO_SETTINGS_MODULE=config.settings.prod` locally would silently
    inherit dev values from .env and bypass prod's no-default env validation.
    """
    source = inspect.getsource(base)
    assert "read_env" not in source, (
        "base.py loads a dotenv file. Move dotenv loading to dev.py only — "
        "prod fails open against a dev .env otherwise."
    )


def test_dev_and_prod_configure_csrf_trusted_origins() -> None:
    """Codex adversarial review of Phase 4 slice 1 identified that Next.js'
    external rewrite uses changeOrigin=true: the proxy rewrites Host for
    Django, but the browser still sends Origin=http://localhost:3000.
    CsrfViewMiddleware._origin_verified() compares Origin to
    scheme://request.get_host(); mismatch 403s every unsafe method unless
    the frontend origin is in CSRF_TRUSTED_ORIGINS. Slice-6's import
    approve/override/reject endpoints would silently 403 otherwise.

    Static check: both dev.py and prod.py must declare CSRF_TRUSTED_ORIGINS.
    Without the declaration, the setting falls back to Django's default of
    `[]` and the proxy is unusable.
    """
    dev_source = inspect.getsource(dev)
    assert "CSRF_TRUSTED_ORIGINS" in dev_source, (
        "dev.py is not configuring CSRF_TRUSTED_ORIGINS — the Next.js dev "
        "proxy will 403 on every unsafe method. Add an "
        "env.list('DJANGO_CSRF_TRUSTED_ORIGINS', default=[...]) entry."
    )
    assert "CSRF_TRUSTED_ORIGINS" in _PROD_SOURCE, (
        "prod.py is not configuring CSRF_TRUSTED_ORIGINS — the prod frontend "
        "will 403 on every unsafe method. Add an "
        "env.list('DJANGO_CSRF_TRUSTED_ORIGINS') entry (no default → fails "
        "closed if forgotten, matching SECRET_KEY/ALLOWED_HOSTS treatment)."
    )


@override_settings(
    CSRF_TRUSTED_ORIGINS=["http://localhost:3000"],
    ALLOWED_HOSTS=["*"],  # let request.get_host() return whatever we set
)
def test_csrf_middleware_accepts_proxied_frontend_origin() -> None:
    """Behavioral pair to the static check above: simulate the request shape
    the Next.js proxy produces (Origin from the browser, Host rewritten by
    changeOrigin=true) and verify CsrfViewMiddleware accepts it given the
    configured CSRF_TRUSTED_ORIGINS. If this fails, the slice-6 write flows
    are blocked — the test is the canary.
    """
    factory = RequestFactory()
    request = factory.post(
        "/api/imports/rows/1/reject/",
        HTTP_ORIGIN="http://localhost:3000",
        HTTP_HOST="backend:8000",  # what Next's changeOrigin=true sends to Django
    )

    def _placeholder_get_response(_req: HttpRequest) -> HttpResponse:
        return HttpResponse()  # never invoked — _origin_verified is direct

    middleware = CsrfViewMiddleware(_placeholder_get_response)
    # _origin_verified is "private" by name but stable Django API (consistent
    # across 4.x/5.x). django-stubs doesn't expose it in the type stubs.
    assert middleware._origin_verified(request), (  # type: ignore[attr-defined]
        "Origin check failed: CSRF_TRUSTED_ORIGINS does not accept the "
        "frontend origin. The /api/* proxy will 403 on every unsafe method "
        "even though the browser sees /api/* as same-origin."
    )


# Dockerfile-style placeholder env: satisfies every fail-closed prod
# validator so config.settings.prod can be imported as a plain module
# (importing it does NOT reconfigure django.conf.settings).
_PROD_IMPORT_ENV = {
    "DJANGO_SECRET_KEY": "prod-import-test-not-real",
    "DJANGO_ALLOWED_HOSTS": "build",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://build",
    "DATABASE_URL": "postgres://build:build@build:5432/build",
    "CELERY_BROKER_URL": "redis://build:6379/1",
    "CELERY_RESULT_BACKEND": "redis://build:6379/2",
}


# Env knobs prod.py reads that a developer's shell might legitimately export
# (they are intended deployment overrides). The import helper unsets any not
# explicitly passed, so the default-posture tests assert hermetically
# (Codex review 2026-06-12).
_PROD_TUNABLE_VARS = (
    "DJANGO_SESSION_COOKIE_SAMESITE",
    "DJANGO_CSRF_COOKIE_SAMESITE",
    "DJANGO_SECURE_SSL_REDIRECT",
)


def _evict_prod_module() -> None:
    """Remove config.settings.prod from BOTH import-machinery caches:
    sys.modules AND the attribute CPython pins on the parent package (which
    would short-circuit a later `from config.settings import prod` with a
    stale module bound to this test's patched env — Codex review 2026-06-12).
    """
    sys.modules.pop("config.settings.prod", None)
    parent = sys.modules.get("config.settings")
    if parent is not None:
        parent.__dict__.pop("prod", None)


def _import_prod(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> ModuleType:
    """Import config.settings.prod under the placeholder env (+ overrides).

    Tunable vars not in `overrides` are unset (hermetic against the runner's
    shell). prod.py mutates the SHARED base.LOGGING dict in place (console
    formatter → "json"), so that is snapshotted and restored, and the module
    is fully evicted afterwards so no other test sees a cached prod import.
    """
    for var, value in {**_PROD_IMPORT_ENV, **overrides}.items():
        monkeypatch.setenv(var, value)
    for var in _PROD_TUNABLE_VARS:
        if var not in overrides:
            monkeypatch.delenv(var, raising=False)
    console_handler = base.LOGGING["handlers"]["console"]
    original_formatter = console_handler["formatter"]
    _evict_prod_module()
    try:
        return importlib.import_module("config.settings.prod")
    finally:
        console_handler["formatter"] = original_formatter
        _evict_prod_module()


def test_prod_pins_explicit_samesite_cookie_posture() -> None:
    """Railway deploy slice 1: prod.py must pin SESSION/CSRF cookie SameSite
    explicitly (env-tunable, default "Lax") rather than inherit it as an
    implicit Django default — the carried cookie-tuning deploy item. Pins
    the env-tunable "Lax" assignment AND that it is the only assignment
    (a later re-assignment weakening it would otherwise win silently).
    The behavioral pair is test_prod_samesite_defaults_are_lax below.
    """
    for setting, env_var in (
        ("SESSION_COOKIE_SAMESITE", "DJANGO_SESSION_COOKIE_SAMESITE"),
        ("CSRF_COOKIE_SAMESITE", "DJANGO_CSRF_COOKIE_SAMESITE"),
    ):
        assignments = re.findall(rf"^{setting}\s*=", _PROD_SOURCE, re.MULTILINE)
        assert len(assignments) == 1, (
            f"prod.py assigns {setting} {len(assignments)} times — exactly one "
            "env-tunable assignment is the contract; a re-assignment below the "
            "pinned one would silently win."
        )
        assert re.search(
            rf'^{setting}\s*=\s*env\(\s*"{env_var}",\s*default="Lax"\s*\)',
            _PROD_SOURCE,
            re.MULTILINE,
        ), (
            f"prod.py no longer pins {setting} to an env-tunable "
            '"Lax" default. The deploy cookie posture must be explicit in '
            "prod settings, not an inherited framework default."
        )


def test_prod_samesite_defaults_are_lax(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behavioral pair to the source pin above: import prod.py under the
    placeholder env and assert the values that actually land."""
    prod_module = _import_prod(monkeypatch)
    assert prod_module.SESSION_COOKIE_SAMESITE == "Lax"
    assert prod_module.CSRF_COOKIE_SAMESITE == "Lax"


def test_prod_uses_database_cache_not_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis was dropped from the deploy topology (2026-06-14). The only runtime
    cache consumer is the login throttle, and DatabaseCache on the existing
    Postgres keeps that bucket global across gunicorn workers without a Redis
    service. Two guarantees:

    1. prod.py must NOT read REDIS_URL — otherwise a no-longer-deployed service
       becomes a fail-closed boot dependency again (Invariant 2).
    2. CACHES must be DatabaseCache(LOCATION="millennium_cache").

    _PROD_IMPORT_ENV deliberately omits REDIS_URL, so a clean import here also
    proves prod boots without it.
    """
    assert "REDIS_URL" not in _PROD_SOURCE, (
        "prod.py still references REDIS_URL — Redis was dropped from the deploy "
        "topology (DatabaseCache on Postgres backs the login throttle). A "
        "REDIS_URL read re-introduces a fail-closed dependency on a service that "
        "is no longer deployed."
    )
    prod_module = _import_prod(monkeypatch)
    cache = prod_module.CACHES["default"]
    assert cache["BACKEND"] == "django.core.cache.backends.db.DatabaseCache", (
        "prod CACHES is not DatabaseCache — the Redis-free deploy backs the "
        "login throttle with the database cache."
    )
    assert cache["LOCATION"] == "millennium_cache"


@pytest.mark.parametrize(
    ("env_var", "bad_value"),
    [
        ("DJANGO_SESSION_COOKIE_SAMESITE", "Laxx"),
        ("DJANGO_SESSION_COOKIE_SAMESITE", "lax"),
        ("DJANGO_CSRF_COOKIE_SAMESITE", ""),
        ("DJANGO_CSRF_COOKIE_SAMESITE", "Lax "),
    ],
)
def test_prod_samesite_validation_fails_closed(
    monkeypatch: pytest.MonkeyPatch, env_var: str, bad_value: str
) -> None:
    """Adversarial review 2026-06-12: Django validates samesite only inside
    set_cookie, so a typo'd value would boot green, pass the platform
    healthcheck (which sets no cookie), then 500 every login — and an EMPTY
    value skips Django's check entirely, silently emitting cookies with no
    SameSite attribute (fail-open). prod.py must refuse both at import.
    """
    with pytest.raises(ImproperlyConfigured, match=env_var):
        _import_prod(monkeypatch, **{env_var: bad_value})


def test_no_settings_module_overrides_cookie_httponly() -> None:
    """Invariant 11 static guard: NO settings module may assign
    SESSION_COOKIE_HTTPONLY or CSRF_COOKIE_HTTPONLY. The Django defaults are
    load-bearing — sessionid must stay HttpOnly (else XSS exfiltrates the
    session) and csrftoken must stay JS-readable (else proxy.ts can't echo
    X-CSRFToken and every unsafe /api/* write 403s). The behavioral pair
    lives in test_auth.py (sessionid HttpOnly) and test_health.py (csrftoken
    not HttpOnly); this catches the override at the source level across
    EVERY module in config/settings/ (globbed from disk, so a future
    settings module is covered automatically), including modules the
    behavioral tests never load. The regex also matches indented and
    type-annotated assignments.
    """
    assignment = re.compile(
        r"^\s*(SESSION|CSRF)_COOKIE_HTTPONLY\s*(?::[^=\n]+)?=", re.MULTILINE
    )
    settings_dir = Path(__file__).resolve().parents[1] / "config" / "settings"
    module_paths = sorted(settings_dir.glob("*.py"))
    assert module_paths, "config/settings/*.py glob found nothing — layout changed?"
    for path in module_paths:
        assert not assignment.search(path.read_text()), (
            f"{path.name} assigns a cookie HttpOnly setting. Invariant 11: "
            "never override SESSION_COOKIE_HTTPONLY (must stay True) or "
            "CSRF_COOKIE_HTTPONLY (must stay False) — both Django defaults "
            "are deliberate."
        )


def test_num_proxies_env_tunable_with_unspoofable_default() -> None:
    """Railway deploy slice 1: the login-throttle proxy depth is env-tunable
    (DJANGO_NUM_PROXIES) for the post-deploy hardening pass, but its DEFAULT
    must stay 0 — DRF keys the throttle on REMOTE_ADDR and ignores the
    client-supplied X-Forwarded-For (the Codex 2026-05-30 rotating-XFF
    bypass). The behavioral pair is
    test_auth.py::test_login_rate_limit_ignores_spoofed_forwarded_for.
    """
    assert base.REST_FRAMEWORK["NUM_PROXIES"] == 0, (
        "Effective NUM_PROXIES is not 0: either the code default changed "
        "(the source assertion below pins that separately) or "
        "DJANGO_NUM_PROXIES is exported in this shell — unset it for a "
        "hermetic run. A nonzero default trusts client-supplied "
        "X-Forwarded-For hops and re-opens the rotating-XFF "
        "bucket-per-request bypass."
    )
    assert (
        '"NUM_PROXIES": _validated_num_proxies(env.int("DJANGO_NUM_PROXIES", default=0))'
        in inspect.getsource(base)
    ), (
        "base.py no longer wires the validated DJANGO_NUM_PROXIES knob into "
        "REST_FRAMEWORK — the post-deploy XFF hardening pass needs it "
        "env-tunable (and boot-validated) without a code change."
    )


def test_validated_num_proxies_rejects_negative() -> None:
    """A negative depth makes DRF's get_ident index the XFF chain with a
    negative offset — an IndexError 500 on every throttled endpoint, only at
    request time. The validator must fail at boot instead.
    """
    with pytest.raises(ImproperlyConfigured, match="DJANGO_NUM_PROXIES"):
        base._validated_num_proxies(-1)
    assert base._validated_num_proxies(0) == 0
    assert base._validated_num_proxies(2) == 2
