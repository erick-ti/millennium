from __future__ import annotations

from pathlib import Path
from typing import Any

import django_stubs_ext
import environ
import structlog
from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured

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
    "apps.status",
    "apps.audit",
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
    # LAST, inside django_structlog's RequestMiddleware: by the time AuditMiddleware runs,
    # request.user / request.session are populated AND the per-request `request_id` is
    # bound to structlog contextvars (so audit rows correlate with the JSON app logs).
    "apps.audit.middleware.AuditMiddleware",
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


def _validated_num_proxies(raw: int) -> int:
    """Fail closed at boot on a negative proxy depth (adversarial review
    2026-06-12): DRF's BaseThrottle.get_ident indexes the X-Forwarded-For
    chain with the configured depth, and a negative value raises IndexError
    at request time — a green-booting deploy that 500s every throttled
    endpoint."""
    if raw < 0:
        raise ImproperlyConfigured(
            f"DJANGO_NUM_PROXIES must be >= 0, got {raw}. Negative values "
            "crash DRF throttle identity derivation at request time."
        )
    return raw


REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    # IsAuthenticated gates every endpoint; DemoReadOnly is ANDed on top as the single
    # chokepoint that read-only-locks the demo showcase account across all endpoints
    # (current + future). Order matters only cosmetically — both must pass; DemoReadOnly
    # can only DENY (demo-account writes), never loosen, so the fail-closed posture holds
    # (Invariant 2). LogoutView opts out so the demo can end its own session; AllowAny
    # views don't inherit defaults. Not an env-sensitive value (a structural default).
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "apps.core.permissions.DemoReadOnly",
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
    # demo_login is a SEPARATE scope from login: the demo endpoint mints a read-only
    # session with no password to brute-force, so it must never drain the credential
    # bucket — its own (looser) cap just stops session-creation spam (one DB session
    # row per call). Same REMOTE_ADDR keying via NUM_PROXIES below.
    # client_error throttles the public frontend-error beacon (POST /api/audit/client-errors/).
    # Like login it keys on REMOTE_ADDR (NUM_PROXIES=0), so behind the proxy it's ONE global
    # bucket — fine: a generous cap absorbs a real error burst while bounding abuse, and the
    # body cap + field truncation + daily retention prune bound the damage either way.
    "DEFAULT_THROTTLE_RATES": {
        "login": "5/min",
        "demo_login": "30/min",
        "client_error": "60/min",
    },
    # NUM_PROXIES=0 makes DRF derive the throttle identity from REMOTE_ADDR and
    # IGNORE the client-supplied X-Forwarded-For. Without it, DRF's default
    # (NUM_PROXIES=None) keys on the *entire XFF header* (throttling.py get_ident),
    # so a client rotating X-Forwarded-For gets a fresh bucket per request and the
    # rate limit never bites (Codex 2026-05-30). With it, the key is the connecting
    # IP — one global, unspoofable bucket, which is exactly right for a single-user
    # app: it caps *total* login attempts/min (CSRF can't stop a direct client).
    # Behind a deployed proxy chain (Railway edge → Next rewrite → Django),
    # REMOTE_ADDR is the connecting upstream hop, so 0 still yields one shared
    # bucket — the safe default. Raise DJANGO_NUM_PROXIES to the verified chain
    # depth ONLY after an empirical spoof test against the live edge (deploy
    # runbook): platform XFF handling changes without notice, and trusting one
    # hop too many re-opens the rotating-XFF bypass this guards against.
    # Default 0 is fail-safe; an env read with a safe default in base is the
    # SYNC_GUARD_* precedent, not an Invariant 2 value. Like SYNC_GUARD_*, it is
    # read at base-import time from the real process env only — a repo-root .env
    # value is inert in dev because dev.py loads dotenv AFTER importing base.
    "NUM_PROXIES": _validated_num_proxies(env.int("DJANGO_NUM_PROXIES", default=0)),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Millennium API",
    "DESCRIPTION": "Yu-Gi-Oh collection portfolio tracker",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    # Schema/docs are recon material — require auth even though DRF default is.
    # drf-spectacular uses its own SERVE_PERMISSIONS, not DEFAULT_PERMISSION_CLASSES.
    # IsNotDemoUser, not bare IsAuthenticated: the read-only demo account is an
    # authenticated session, so plain IsAuthenticated would expose the full API surface
    # to it (and thus, effectively, to recruiters). IsNotDemoUser still requires auth
    # (anonymous → 403, Invariant 7), and additionally keeps the demo out — a
    # strengthening. The SPA fetches the schema offline at build, so no runtime cost.
    "SERVE_PERMISSIONS": ["apps.core.permissions.IsNotDemoUser"],
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
# a *partial* loss slip under it, so the sync instead fails
# closed when ONE run would null archetype on more than this FRACTION of the currently
# tagged cards. A small absolute floor (in cards/sync.py) keeps early/small states and
# legitimate handful-of-card corrections from tripping it.
# ---------------------------------------------------------------------------

SYNC_GUARD_METADATA_TOLERANCE = env.float("SYNC_GUARD_METADATA_TOLERANCE", default=0.02)
SYNC_GUARD_PRICING_TOLERANCE = env.float("SYNC_GUARD_PRICING_TOLERANCE", default=0.10)
SYNC_GUARD_ARCHETYPE_TOLERANCE = env.float("SYNC_GUARD_ARCHETYPE_TOLERANCE", default=0.05)

# ---------------------------------------------------------------------------
# Status dashboard
#
# The deployed commit, baked into the image at build time (deploy.sh passes
# --build-arg GIT_SHA=$(git rev-parse --short HEAD); the Dockerfile copies it to
# ENV GIT_SHA) and surfaced by /api/status/. An OPTIONAL display value with a safe
# default (the NUM_PROXIES precedent — read from the real process env, so a
# repo-root .env is inert in dev), NOT a fail-closed required var: a local/CI build
# that passes no build-arg shows "unknown", which is correct, not a boot failure.
#
# Healthchecks.io read API (the flow's backup + CD dead-man nodes). All OPTIONAL,
# safe defaults: without the key the /api/status/checks/ tier degrades to
# "not configured" (no network call) and those flow nodes render grey. The two
# checks are identified by their Healthchecks SLUG (must be UNIQUE within the project)
# so a co-tenant check in the same project is not surfaced. The read-only key returns
# no ping URLs (no secret leak).
# STATUS_CACHE_TTL bounds how long external-provider responses are cached.
# ---------------------------------------------------------------------------

GIT_SHA = env.str("GIT_SHA", default="unknown")
HEALTHCHECKS_READ_API_KEY = env.str("HEALTHCHECKS_READ_API_KEY", default="")
HEALTHCHECKS_BACKUP_SLUG = env.str("HEALTHCHECKS_BACKUP_SLUG", default="")
HEALTHCHECKS_CD_SLUG = env.str("HEALTHCHECKS_CD_SLUG", default="")
STATUS_CACHE_TTL = env.int("STATUS_CACHE_TTL", default=60)

# ---------------------------------------------------------------------------
# Audit / error-log retention + the public-beacon abuse bound (apps.audit)
#
# How long the append-only AuditEvent / ErrorLog rows are kept before the daily
# `prune_audit` timer deletes them. OPTIONAL with safe defaults (the NUM_PROXIES /
# GIT_SHA precedent — read from the real process env), but VALIDATED positive: a <=0
# value FAILS CLOSED at boot (_validated_retention_days), because prune deletes rows
# older than now-days, so days<=0 makes the cutoff now-or-future and would irreversibly
# wipe the append-only store. Audit-of-the-owner is kept a
# year; backend error noise 90 days; FRONTEND errors only 30 days because the frontend
# beacon is a PUBLIC unauthenticated write surface, so its rows are the cheapest to abuse
# and the least valuable to retain long.
#
# MAX_PUBLIC_FRONTEND_ERRORS_PER_DAY hard-caps how many PUBLIC frontend beacons are
# persisted per UTC day. "Public" = anonymous OR the read-only demo: the demo session is
# publicly obtainable in one click (POST /api/auth/demo-login/ is AllowAny), so it is NOT a
# trust boundary for this write surface. The `client_error`
# throttle (60/min) bounds the burst RATE, but CSRF is not a bot defense (the seed endpoint
# is public + the cookie is JS-readable), so a scripted client — anonymous OR holding a demo
# cookie — could otherwise accumulate ~86k rows/day until the prune window, a disk-fill risk
# on the shared box. This quota + the 30-day frontend retention bound the public frontend
# store to roughly cap*30 rows. Only a REAL (non-demo) account is exempt, so genuine owner
# reports are never dropped.
# ---------------------------------------------------------------------------

def _validated_retention_days(name: str, raw: int) -> int:
    """Fail closed at boot on a non-positive retention window (the _validated_num_proxies
    precedent): prune deletes rows older than now-days, so days<=0 makes the cutoff now-or-
    future and would irreversibly wipe the append-only audit/error store."""
    if raw <= 0:
        raise ImproperlyConfigured(
            f"{name} must be >= 1, got {raw}. A non-positive retention window makes the "
            "prune cutoff now-or-future and would delete the entire audit/error store."
        )
    return raw


AUDIT_EVENT_RETENTION_DAYS = _validated_retention_days(
    "AUDIT_EVENT_RETENTION_DAYS", env.int("AUDIT_EVENT_RETENTION_DAYS", default=365)
)
ERROR_LOG_RETENTION_DAYS = _validated_retention_days(
    "ERROR_LOG_RETENTION_DAYS", env.int("ERROR_LOG_RETENTION_DAYS", default=90)
)
FRONTEND_ERROR_LOG_RETENTION_DAYS = _validated_retention_days(
    "FRONTEND_ERROR_LOG_RETENTION_DAYS",
    env.int("FRONTEND_ERROR_LOG_RETENTION_DAYS", default=30),
)
MAX_PUBLIC_FRONTEND_ERRORS_PER_DAY = env.int("MAX_PUBLIC_FRONTEND_ERRORS_PER_DAY", default=2000)

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
