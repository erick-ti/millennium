from __future__ import annotations

from typing import Any

from django.db import models

from apps.cards.normalization import normalize_name
from apps.core.models import TimeStampedModel


class Card(TimeStampedModel):
    """A unique Yu-Gi-Oh card identity — one row per distinct card.

    The surrogate ``id`` is the system identity. ``passcode`` is the Konami
    passcode (YGOPRODeck's ``id``), nullable because TCGCSV-only entities such
    as Tokens have no passcode; it is unique when present.
    """

    passcode = models.BigIntegerField(null=True, blank=True, unique=True)
    name = models.CharField(max_length=255, db_index=True)
    # Derived from ``name`` via ``normalize_name`` on every save, so it can't
    # drift. Indexed, not unique: names may collide after normalization;
    # ``passcode`` is the real identity.
    normalized_name = models.CharField(max_length=255, db_index=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.normalized_name = normalize_name(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name
