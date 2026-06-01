from __future__ import annotations

from pathlib import Path
from typing import Any

import django_stubs_ext
import environ
import structlog
from celery.schedules import crontab

# Make django-stubs' generic ModelAdmin/QuerySet/Manager subscriptable at runtime
# (not just for mypy), so `ModelAdmin[Card]` doesn't raise. Main dep → holds in --no-dev prod.
django_stubs_ext.monkeypatch()

BASE_DIR = Path(__file__).resolve().parents[2]

# `env` is shared by all settings modules. Dotenv loading is intentionally
# NOT done here — it lives in dev.py only. If base.py loaded .env, then a
# developer running `DJANGO_SETTINGS_MODULE=config.settings.prod` locally
# would silently inherit dev values from .env and bypass prod's fail-closed
# env validation.
env = environ.Env()

# ---------------------------------------------------------------------------
# Core
#
# Settings intentionally NOT set here (each environment-specific module sets
# them so prod fails closed when env vars are missing):
#   - SECRET_KEY
#   - DATABASES
#   - CACHES
#   - CELERY_BROKER_URL / CELERY_RESULT_BACKEND
# dev/test provide safe local defaults; prod reads env vars with no fallback.
# ---------------------------------------------------------------------------

DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "drf_spectacular",
    "django_structlog",
]

LOCAL_APPS = [
    "apps.core",
    "apps.cards",
    "apps.pricing",
    "apps.portfolio",
    "apps.collection",
    "apps.imports",
    "apps.valuation",
    "apps.alerts",
    "apps.decks",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise must come immediately after SecurityMiddleware — see WN docs.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_structlog.middlewares.RequestMiddleware",
]

# ---------------------------------------------------------------------------
# URLs / WSGI / ASGI
# ---------------------------------------------------------------------------

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# i18n / time
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Default storage uses Django's stdlib backend; prod overrides to WhiteNoise's
# CompressedManifestStaticFilesStorage for hashed filenames + far-future caching.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# DRF + drf-spectacular
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    # List endpoints (the imports review queue can return a full collection's worth of rows)
    # page by default; the response shape is {count, next, previous, results}. Not an
    # environment-sensitive value (Invariant 2) — a framework default, safe in base.
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
    # Login is the one anonymous credential surface (every other endpoint is
    # IsAuthenticated), so it's the only brute-force target. LoginView applies a
    # ScopedRateThrottle("login"); the rate is app config, not an env secret
    # (Invariant 2 — like PAGE_SIZE), and needs a real cache (prod/dev: Redis;
    # test: LocMem). This is a best-effort SPEED BUMP, not the security boundary
    # (the password + AUTH_PASSWORD_VALIDATORS are): DRF's cache throttle is
    # non-atomic (get→check→set), so a concurrent burst can exceed the window.
    # Accepted for a single-user app — atomic rate-limiting / account lockout is a
    # deliberate non-goal (Codex 2026-05-30); revisit only if it goes multi-user.
    "DEFAULT_THROTTLE_RATES": {"login": "5/min"},
    # NUM_PROXIES=0 makes DRF derive the throttle identity from REMOTE_ADDR and
    # IGNORE the client-supplied X-Forwarded-For. Without it, DRF's default
    # (NUM_PROXIES=None) keys on the *entire XFF header* (throttling.py get_ident),
    # so a client rotating X-Forwarded-For gets a fresh bucket per request and the
    # rate limit never bites (Codex 2026-05-30). With it, the key is the connecting
    # proxy's IP — one global, unspoofable bucket, which is exactly right for a
    # single-user app: it caps *total* login attempts/min (CSRF can't stop a direct
    # client). The edge proxy should also strip/overwrite inbound XFF (deploy note).
    "NUM_PROXIES": 0,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Millennium API",
    "DESCRIPTION": "Yu-Gi-Oh collection portfolio tracker",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    # Schema/docs are recon material — require auth even though DRF default is.
    # drf-spectacular uses its own SERVE_PERMISSIONS, not DEFAULT_PERMISSION_CLASSES.
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAuthenticated"],
}

# ---------------------------------------------------------------------------
# Celery (broker/backend URLs set by env-specific modules — see top of file)
# ---------------------------------------------------------------------------

CELERY_TASK_ALWAYS_EAGER = False
CELERY_TIMEZONE = TIME_ZONE
# Daily UTC schedule (CELERY_TIMEZONE = TIME_ZONE = "UTC"). Metadata runs first (02:00)
# so the printings it seeds exist before the pricing reconcile (03:00) matches against
# them; then valuation (04:00) rolls the day's prices into portfolio snapshots. The hour
# gaps also keep each run's logs and failures independent. Metadata->pricing is a *soft*
# dependency (an ordering, not a chain): reconciliation tolerates stale metadata
# (genuinely new printings are review-queued, not lost) and both syncs are idempotent.
# Valuation->pricing is a *hard* dependency: the 04:00 slot is only a hint -- run_valuation
# refuses unless a successful same-day pricing SyncRun is recorded, because a slow ingest
# could overrun 04:00 and valuing a partial price table writes an uncorrectable snapshot
# (DECISIONS 2026-05-24 slice 3, 2026-05-25 slice 4c). Alerts (05:00) likewise gates on a
# same-day pricing SUCCESS, not the clock -- it reads the same price table for its two-anchor
# move (Phase 5 slice 4); it runs after valuation by convention but depends only on pricing.
CELERY_BEAT_SCHEDULE: dict[str, Any] = {
    "ygoprodeck-metadata-daily": {
        "task": "cards.sync_ygoprodeck_metadata",
        "schedule": crontab(hour=2, minute=0),
    },
    "tcgcsv-pricing-daily": {
        "task": "pricing.sync_tcgcsv_pricing",
        "schedule": crontab(hour=3, minute=0),
    },
    "valuation-daily": {
        "task": "valuation.value_portfolios",
        "schedule": crontab(hour=4, minute=0),
    },
    "alerts-daily": {
        "task": "alerts.compute_alerts",
        "schedule": crontab(hour=5, minute=0),
    },
}

# ---------------------------------------------------------------------------
# Sync cardinality guard (DECISIONS 2026-05-24 slice 3)
#
# The recurring daily syncs reject a fetch that shrank below
# `last_good * (1 - tolerance)` vs the last successful run (recorded in
# core.SyncRun), catching a truncated bulk dump before it overwrites a good
# catalog. Metadata card count is ~monotonic (Konami never un-releases), so a
# tight bound is safe; TCGCSV price-row coverage fluctuates day-to-day (a product
# has a price row only when TCGplayer reports one), so prices need more slack.
# Archetype (Phase 5) is guarded differently — not by a fetch-count floor but by the
# WITHDRAWAL it would cause: it's a single OPTIONAL field, so a degraded fetch (the key
# dropped for some or all cards) would null existing tags. An aggregate count floor lets
# a *partial* loss slip under it (Codex adversarial review), so the sync instead fails
# closed when ONE run would null archetype on more than this FRACTION of the currently
# tagged cards. A small absolute floor (in cards/sync.py) keeps early/small states and
# legitimate handful-of-card corrections from tripping it.
# ---------------------------------------------------------------------------

SYNC_GUARD_METADATA_TOLERANCE = env.float("SYNC_GUARD_METADATA_TOLERANCE", default=0.02)
SYNC_GUARD_PRICING_TOLERANCE = env.float("SYNC_GUARD_PRICING_TOLERANCE", default=0.10)
SYNC_GUARD_ARCHETYPE_TOLERANCE = env.float("SYNC_GUARD_ARCHETYPE_TOLERANCE", default=0.05)

# ---------------------------------------------------------------------------
# Logging — structlog + stdlib bridge
# ---------------------------------------------------------------------------

LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "structlog.stdlib.ProcessorFormatter",
            "processor": structlog.processors.JSONRenderer(),
        },
        "console": {
            "()": "structlog.stdlib.ProcessorFormatter",
            "processor": structlog.dev.ConsoleRenderer(colors=True),
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django_structlog": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "apps": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
