"""Test settings against a real PostgreSQL engine.

Identical to ``test`` (fast MD5 hasher, eager Celery, locmem cache) but with a
PostgreSQL ``DATABASES``, so migrations run on the production engine and the
``CardPrinting`` natural-key constraint (``UniqueConstraint(nulls_distinct=False)``,
which sqlite silently skips) is actually created and enforced. Used by the
``tests`` CI job; locally: ``pytest --ds=config.settings.test_postgres`` against
the compose Postgres (``DATABASE_URL`` defaults to the loopback-published port).
"""

from .base import env
from .test import *  # noqa: F403

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://postgres:postgres@localhost:5432/millennium",
    ),
}
