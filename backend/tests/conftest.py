from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _isolate_cache() -> Iterator[None]:
    """DRF throttle counters live in the cache (LocMem in tests, shared across the
    process), so clear it around every test — otherwise the login rate-limit test
    would leak its filled bucket and 429 a later test's legitimate login."""
    cache.clear()
    yield


@pytest.fixture
def user(db: None) -> User:
    """A real (NOT force-authenticated) user for the auth suite's session-cookie
    round-trip. The per-API-file ``client`` fixtures keep their local
    ``force_authenticate`` shortcut; this one exists so ``test_auth.py`` can log
    in for real. Tests consuming it still carry ``@pytest.mark.django_db``."""
    return get_user_model().objects.create_user(
        username="reader", email="r@example.com", password="correct-horse-battery"
    )
