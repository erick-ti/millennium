import environ

from .base import *  # noqa: F403
from .base import BASE_DIR, env

# Local .env is loaded ONLY in dev. Prod/test never see it.
environ.Env.read_env(BASE_DIR.parent / ".env")

DEBUG = True
ALLOWED_HOSTS = ["*"]

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-not-secret-do-not-use-in-prod")

# Same-origin /api/* requests from the Next.js dev proxy at localhost:3000:
# the browser sees same-origin (so it sends an Origin header), but Next's
# external rewrite uses changeOrigin=true, so Django receives Host=backend:8000
# (or localhost:8000) while the browser still sends Origin=http://localhost:3000.
# CsrfViewMiddleware._origin_verified() builds good_origin=scheme://request.get_host()
# and compares to HTTP_ORIGIN; the mismatch 403s every unsafe method unless the
# frontend origin is whitelisted here. Found by Codex adversarial review of
# Phase 4 slice 1.
CSRF_TRUSTED_ORIGINS = env.list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=["http://localhost:3000"],
)

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://postgres:postgres@localhost:5432/millennium",
    ),
}

CACHES = {
    "default": env.cache("REDIS_URL", default="redis://localhost:6379/0"),
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/2")

# WhiteNoise: serve from app static dirs in dev so collectstatic isn't required.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True
