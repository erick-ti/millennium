"""Healthchecks.io tier — the box's backup + CD dead-man checks.

External, OPTIONAL, and cached. Degrades to ``configured: false`` without a read-API
key and to ``available: false`` on any provider error, so the dashboard's flow nodes
(backup, CD) never hang or 500 the request — they just render grey. The two checks are
identified by their Healthchecks *slug* (env-configured), so an unrelated co-tenant
check in the same project is not surfaced — PROVIDED each configured slug is unique
within the project (Healthchecks v3 does not enforce slug uniqueness; a duplicate
collapses last-wins). Only a sanitized summary leaves this module (name / status /
last-ping / ping-count) — never the ping URL or the read-key's unique_key (credentials).
"""

from __future__ import annotations

from typing import Any

import httpx
from django.conf import settings
from django.core.cache import cache

_HEALTHCHECKS_API = "https://healthchecks.io/api/v3/checks/"
_TIMEOUT = httpx.Timeout(5.0)
_USER_AGENT = "millennium/0.1 (status dashboard)"
_CACHE_KEY = "status:healthchecks"
# Cache a FAILURE only briefly so a transient provider blip recovers fast, but a
# success for the full TTL to spare the rate limit (Healthchecks allows plenty, but
# the dashboard polls and there's no reason to call per request).
_ERROR_TTL = 15

_NOT_CONFIGURED: dict[str, Any] = {
    "configured": False,
    "available": False,
    "error": None,
    "backup": None,
    "cd": None,
}


def _fetch_raw(api_key: str) -> list[Any]:
    """GET the project's checks from the Healthchecks read API. Returns the raw
    ``checks`` list; raises on transport / HTTP errors (the caller degrades)."""
    response = httpx.get(
        _HEALTHCHECKS_API,
        timeout=_TIMEOUT,
        headers={"X-Api-Key": api_key, "User-Agent": _USER_AGENT},
    )
    response.raise_for_status()
    checks = response.json().get("checks", [])
    return checks if isinstance(checks, list) else []


def _row(check: Any) -> dict[str, Any] | None:
    """Sanitized summary of one check — an explicit allowlist (name / status /
    last_ping / n_pings), NEVER the ping URL or the read-key's ``unique_key``. The
    int() coercion is guarded: a malformed (non-numeric) ``n_pings`` from an
    unexpected provider response salvages to 0 rather than raising into the view."""
    if not isinstance(check, dict):
        return None
    try:
        n_pings = int(check.get("n_pings", 0) or 0)
    except (TypeError, ValueError):
        n_pings = 0
    return {
        "name": str(check.get("name", ""))[:200],
        "status": str(check.get("status", "new")),
        "last_ping_at": check.get("last_ping"),
        "n_pings": n_pings,
    }


def build_checks_status() -> dict[str, Any]:
    """The Healthchecks tier, mapped to the flow's backup + CD nodes by slug. Cached;
    optional (degrades, never 500s). Mirrors the ``configured``/``available`` shape of
    the other external tier so the frontend renders one graceful-degradation path."""
    api_key = settings.HEALTHCHECKS_READ_API_KEY
    if not api_key:
        return dict(_NOT_CONFIGURED)

    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    try:
        checks = _fetch_raw(api_key)
        # Key by slug, DROPPING empty/None slugs (a Healthchecks v3 co-tenant check
        # without a manually-set slug arrives as slug="" — it must never become a key).
        by_slug = {
            slug: c for c in checks if isinstance(c, dict) and (slug := c.get("slug"))
        }
        # An UNSET configured slug (default "") must not resolve to anything — guard it
        # explicitly so the co-tenant isolation can't be refactored away (the previous
        # implicit `slug or None` looked redundant). The mapping is INSIDE the try so a
        # malformed body raising in _row degrades gracefully like a fetch error.
        backup_slug = settings.HEALTHCHECKS_BACKUP_SLUG
        cd_slug = settings.HEALTHCHECKS_CD_SLUG
        result: dict[str, Any] = {
            "configured": True,
            "available": True,
            "error": None,
            "backup": _row(by_slug.get(backup_slug)) if backup_slug else None,
            "cd": _row(by_slug.get(cd_slug)) if cd_slug else None,
        }
    except Exception as exc:  # any transport/HTTP/parse/mapping error degrades, never 500s
        result = {
            "configured": True,
            "available": False,
            "error": type(exc).__name__,
            "backup": None,
            "cd": None,
        }
        cache.set(_CACHE_KEY, result, _ERROR_TTL)
        return result

    cache.set(_CACHE_KEY, result, settings.STATUS_CACHE_TTL)
    return result
