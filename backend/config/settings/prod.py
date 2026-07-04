from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import LOGGING, env

DEBUG = False

# Required env vars: no defaults, so missing values fail closed.
SECRET_KEY = env("DJANGO_SECRET_KEY")
# Two Host values must be listed when a load balancer / health probe sits in
# front of the backend: the host the health probe sends (a platform that probes
# plain HTTP with a probe-specific Host not listed here fails every deploy with a
# 400 before the app serves traffic) AND the host every real proxied request
# carries (the Next rewrite sends the *destination* Host to the backend; a
# missing entry yields a deploy marked healthy while every /api/* request 400s
# with DisallowedHost). Never "*".
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

# Frontend origin(s) the Django CSRF middleware accepts for /api/* POSTs.
# Required in prod (no default → fails closed if forgotten, matching
# SECRET_KEY/ALLOWED_HOSTS treatment). See dev.py for the failure mode:
# without this, every unsafe method through the Next.js proxy 403s because
# Origin (frontend) ≠ scheme://request.get_host() (backend).
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS")

DATABASES = {"default": env.db("DATABASE_URL")}
# DatabaseCache on the existing Postgres, NOT Redis. Under the deploy topology
# (scheduled work runs as standalone cron / systemd-timer management commands,
# never a Celery worker), nothing dials a broker, and the ONLY runtime cache
# consumer is the login ScopedRateThrottle. Backing it with the database keeps
# that throttle bucket GLOBAL across gunicorn workers without standing up a
# Redis service. Requires a one-time `manage.py createcachetable` to create the
# `millennium_cache` table; the command is idempotent (safe to re-run each
# deploy). Redis was dropped. It was deployment inertia from the
# Celery-worker era; re-add it + repoint CACHES here if a real worker returns.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "millennium_cache",
    }
}

# Celery broker/result are configured but never dialed under the cron topology
# (no worker/beat process; the management commands call run_* directly). Kept as
# required env (invariant 2 in ARCHITECTURE.md) so re-introducing a worker is an env change, not a
# code change, set harmless `memory://` placeholders in the deploy env.
CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")

# Hashed filenames + gzip/brotli compression. Requires `collectstatic` to have run.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Production logs as JSON for structured ingestion
LOGGING["handlers"]["console"]["formatter"] = "json"

# Security
# Default ON: a directly-reachable backend should redirect HTTP→HTTPS. Set
# DJANGO_SECURE_SSL_REDIRECT=False on a private-only topology (e.g. Railway
# private networking): TLS terminates at the public edge in front of the
# frontend, the backend is unreachable from the internet, and the platform
# healthcheck probes plain HTTP, a 301 answer fails the probe (it expects
# 200) and with it every deploy.
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Cookie SameSite, pinned explicitly (and env-tunable, the HSTS pattern)
# rather than inherited as an implicit Django default, so the deploy posture
# is visible here. "Lax" is correct for the same-origin proxy architecture:
# the browser only ever talks to the frontend origin and /api/* is
# same-origin there, so Lax costs nothing and blocks cross-site POSTs at the
# cookie layer. Override to "None" ONLY for a genuinely cross-site frontend
# (requires the Secure flags above; weakens the SameSite leg of CSRF defense,
# the token check remains). Do NOT set SESSION_COOKIE_HTTPONLY or
# CSRF_COOKIE_HTTPONLY here, the Django defaults are load-bearing
# (invariant 11 in ARCHITECTURE.md: sessionid stays HttpOnly, csrftoken stays JS-readable for
# proxy.ts) and a static test pins their absence from this module.
SESSION_COOKIE_SAMESITE = env("DJANGO_SESSION_COOKIE_SAMESITE", default="Lax")
CSRF_COOKIE_SAMESITE = env("DJANGO_CSRF_COOKIE_SAMESITE", default="Lax")

# Fail closed at boot on a bad SameSite value.
# Django validates samesite only inside set_cookie, so a typo'd value
# ("Laxx", "Lax " with a stray space from a platform env UI) boots green,
# passes the healthcheck (which sets no cookie), and then 500s every login,
# and an EMPTY value ("clearing" the var in a UI) skips Django's check
# entirely, silently emitting cookies with no SameSite attribute at all.
# Exact canonical values only, matching the adjacent env.bool/env.int knobs'
# fail-at-import behavior.
for _samesite_var, _samesite_value in (
    ("DJANGO_SESSION_COOKIE_SAMESITE", SESSION_COOKIE_SAMESITE),
    ("DJANGO_CSRF_COOKIE_SAMESITE", CSRF_COOKIE_SAMESITE),
):
    if _samesite_value not in ("Lax", "Strict", "None"):
        raise ImproperlyConfigured(
            f"{_samesite_var} must be exactly 'Lax', 'Strict', or 'None'; "
            f"got {_samesite_value!r}. Django only validates this at "
            "set_cookie time, so a bad value would deploy green and then "
            "fail every cookie-setting response."
        )

# HSTS: defaults are conservative (off). Increase deliberately AFTER you have
# verified that HTTPS is served correctly across every subdomain you own.
# Once a browser sees a long-duration header (especially with
# includeSubDomains and preload), recovery requires waiting out the timer.
# Submitting to the HSTS preload list bakes the policy into browsers globally.
#
# LIVE TOPOLOGY NOTE (Hetzner self-host, 2026-06-16): the actual HSTS ramp is
# implemented at the CADDY EDGE (infra/hetzner/edge/caddy/Caddyfile), NOT here. Django
# sits behind the Next /api/* rewrite, which does not propagate X-Forwarded-Proto,
# so request.is_secure() is False at the backend and SecurityMiddleware would emit
# NOTHING even with SECONDS>0, and Django only sees /api/*, never the HTML page
# loads. These knobs are kept (off) for a topology where Django itself terminates
# or directly sees TLS (e.g. the evaluated Railway path); on Hetzner, ramp Caddy.
#
# Suggested rampup once HTTPS is proven (mirrored by the Caddyfile ladder):
#   SECONDS=300 → 86400 (1 day) → 31536000 (1 year)
#   then INCLUDE_SUBDOMAINS=true once every subdomain is HTTPS-only
#   then PRELOAD=true and submit to https://hstspreload.org
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False
)
SECURE_HSTS_PRELOAD = env.bool("DJANGO_SECURE_HSTS_PRELOAD", default=False)
