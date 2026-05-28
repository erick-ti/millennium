"""Regression tests for cross-environment settings safety."""

import inspect
from pathlib import Path

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
