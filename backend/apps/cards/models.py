from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class Card(TimeStampedModel):
    """A unique Yu-Gi-Oh card identity — one row per distinct card.

    The surrogate ``id`` is the system identity. ``passcode`` is the Konami
    passcode (YGOPRODeck's ``id``), nullable because TCGCSV-only entities such
    as Tokens have no passcode; it is unique when present.
    """

    passcode = models.BigIntegerField(null=True, blank=True, unique=True)
    name = models.CharField(max_length=255, db_index=True)
    # Denormalized search key (lowercased, accent-stripped, entity-decoded).
    # Deliberately non-unique: Konami may ship names that collide after
    # normalization; ``passcode`` is the real identity.
    normalized_name = models.CharField(max_length=255, db_index=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
