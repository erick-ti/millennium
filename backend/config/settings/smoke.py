"""Settings for the Playwright end-to-end smoke suite (Phase 5 slice 6).

A live multi-process server (a `runserver` process serving the browser, plus a
separate `migrate` / `seed_smoke` process priming the DB) needs a real on-disk
database shared across processes, so this inherits `dev.py` (Postgres via
`DATABASE_URL`, `DEBUG=True`, `ALLOWED_HOSTS=['*']`, env-driven
`CSRF_TRUSTED_ORIGINS`) rather than `test`/`test_postgres` (sqlite `:memory:` is
per-connection; their `LocMemCache` is per-process).

Two deliberate overrides on top of dev:

1. **`CACHES` → LocMemCache.** The smoke needs no Celery/Redis (imports run
   synchronously inline, no beat), so dropping the Redis dependency keeps the
   CI job to a single Postgres service and lets a developer run it without
   Redis up. The only cache consumer in the request path is the login
   `ScopedRateThrottle`, which is relaxed below, so a per-process bucket is
   irrelevant.

2. **The `login` throttle is relaxed.** The 5/min default (`base.py`) would 429
   a suite that logs in across specs/retries. Relaxing it HERE (not in
   dev/prod) keeps the security posture intact everywhere else. The other scopes
   (e.g. `demo_login`) are carried through from base so their endpoints don't
   raise `ImproperlyConfigured` (a missing scope rate → 500) under smoke.

Invariants 1, 2, and 3 in ARCHITECTURE.md hold: `base.py` still defines no env-sensitive values and
loads no dotenv; this module owns its overrides like dev/prod/test. It inherits
dev's defaults, so it introduces no new fail-closed prod env var and needs no
Dockerfile `collectstatic` placeholder (the placeholder requirement applies only to
prod-required vars). It is NEVER prod: do not run a deployed server under it.
"""

from typing import cast

from .base import REST_FRAMEWORK as _BASE_REST_FRAMEWORK
from .dev import *  # noqa: F403

# Drop the Redis cache dependency, see the module docstring. LocMem is process
# -local, which is fine because the only request-path cache user (the login
# throttle) is relaxed just below.
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
}

# Relax the login throttle so a Playwright suite logging in repeatedly (across
# specs / retries) is never 429'd. Spread the base dict so the rest of the DRF
# config is untouched; the relaxation lives ONLY here, never in dev/prod.
REST_FRAMEWORK = {**_BASE_REST_FRAMEWORK}
# Spread base's rates and bump ONLY login, keep every other scope (demo_login, and any
# future one) so a ScopedRateThrottle endpoint never hits a missing-scope KeyError → 500.
# base's REST_FRAMEWORK is dict[str, object], so the rates entry needs a cast for mypy.
_base_rates = cast("dict[str, str]", _BASE_REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {**_base_rates, "login": "100000/min"}
