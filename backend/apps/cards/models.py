from __future__ import annotations

from typing import Any

from django.db import models
from django.db.models.functions import Trim

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


class CardPrinting(TimeStampedModel):
    """A specific printing of a card: this artwork, at this rarity, in this set.

    Identified by the natural key ``(card, set_code, set_rarity, variant_label)``.
    ``set_code`` alone is never unique in modern sets, and ``(set_code, set_rarity)``
    still collides for variant artworks sharing a rarity (e.g. several "Version"
    prints at Common), so ``variant_label`` disambiguates the rare alt-art case.
    Edition-agnostic: edition is a dimension of ``collection_items`` and
    ``price_snapshots``, never part of printing identity.
    """

    card = models.ForeignKey(Card, on_delete=models.PROTECT, related_name="printings")
    set_code = models.CharField(max_length=32, db_index=True)
    set_rarity = models.CharField(max_length=64)
    # Nullable free text (e.g. "alt art", "Version 1"); NULL means "no variant".
    # null=True is deliberate: the natural-key constraint below is NULLS NOT
    # DISTINCT, so NULL is the single canonical "no variant" value — and save()
    # coerces ""/whitespace to NULL so it can't become a second one.
    variant_label = models.CharField(max_length=128, null=True, blank=True)  # noqa: DJ001
    # Denormalized human-readable set name (e.g. "Quarter Century Stampede").
    set_name = models.CharField(max_length=255)

    class Meta:
        ordering = ["set_code", "set_rarity"]
        constraints = [
            models.UniqueConstraint(
                fields=["card", "set_code", "set_rarity", "variant_label"],
                name="unique_card_printing_natural_key",
                nulls_distinct=False,
            ),
            # Canonical-form guard for variant_label: NULL, or a trimmed non-empty
            # string. save() coerces ""/whitespace to NULL on the instance path, but
            # bulk_create / QuerySet.update / raw SQL bypass save() — without this a
            # stray "" or "   " would slip in as a second "no variant" value beside
            # NULL and defeat the natural key above. Unlike that NULLS NOT DISTINCT
            # constraint (which sqlite skips), a CHECK is enforced on every backend.
            # (SQL TRIM strips spaces only; save()'s str.strip() also covers tabs/
            # newlines, which don't occur in real set variant labels.)
            models.CheckConstraint(
                condition=models.Q(variant_label__isnull=True)
                | (models.Q(variant_label=Trim("variant_label")) & ~models.Q(variant_label="")),
                name="card_printing_variant_label_canonical",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.variant_label is not None:
            self.variant_label = self.variant_label.strip() or None
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        parts = [self.set_code, self.set_rarity]
        if self.variant_label:
            parts.append(self.variant_label)
        return " / ".join(parts)
