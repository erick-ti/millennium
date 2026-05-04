from .base import *  # noqa: F403
from .base import LOGGING, env

DEBUG = False

# Required env vars — no defaults, so missing values fail closed.
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

DATABASES = {"default": env.db("DATABASE_URL")}
CACHES = {"default": env.cache("REDIS_URL")}
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
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# HSTS — defaults are conservative (off). Increase deliberately AFTER you have
# verified that HTTPS is served correctly across every subdomain you own.
# Once a browser sees a long-duration header (especially with
# includeSubDomains and preload), recovery requires waiting out the timer.
# Submitting to the HSTS preload list bakes the policy into browsers globally.
#
# Suggested rampup once HTTPS is proven:
#   SECONDS=300 → 86400 (1 day) → 31536000 (1 year)
#   then INCLUDE_SUBDOMAINS=true once every subdomain is HTTPS-only
#   then PRELOAD=true and submit to https://hstspreload.org
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False
)
SECURE_HSTS_PRELOAD = env.bool("DJANGO_SECURE_HSTS_PRELOAD", default=False)
