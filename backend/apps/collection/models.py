from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class StorageLocation(TimeStampedModel):
    """A physical place a collection is kept — e.g. "Binder A page 3",
    "Deck box #2", "Safe deposit box".

    Distinct from ``Portfolio``, which is a *logical* grouping (DECISIONS
    2026-05-18): a holding's portfolio and its physical location are two
    independent dimensions. Unlike a portfolio, a storage location is NOT
    find-or-created from the Dragon Shield import — the user creates it and
    assigns it manually, and ``collection_items`` will reference it via a
    *nullable* FK. ``name`` is unique to prevent duplicate physical-location
    entries and to give that FK a clean autocomplete target; there is no
    normalized form because nothing matches it against import text.
    """

    name = models.CharField(max_length=255, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
