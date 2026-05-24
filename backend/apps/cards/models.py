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
        # A partial update that touches `name` (e.g. update_or_create issues
        # save(update_fields={"name", ...})) would otherwise write `name` but
        # drop the recomputed `normalized_name`, silently desyncing the two in
        # the DB — the exact drift this derivation exists to prevent (DECISIONS
        # 2026-05-20). Carry normalized_name along whenever name is being saved.
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "name" in update_fields:
            kwargs["update_fields"] = {*update_fields, "normalized_name"}
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


class MetadataSource(models.TextChoices):
    """Catalog/metadata providers that seed provisional printings. Distinct from
    the pricing ``Provider`` enum: a metadata source carries card identity, not
    prices, and YGOPRODeck (metadata-only) is not a pricing provider. Kept as its
    own column on ``PrintingAlias`` so a second metadata source needs no remodel.
    """

    YGOPRODECK = "ygoprodeck", "YGOPRODeck"


class PrintingAlias(TimeStampedModel):
    """Maps a metadata source's *provisional* printing key to the canonical
    ``CardPrinting`` after TCGCSV rarity reconciliation.

    YGOPRODeck seeds printings with a provisional ``set_rarity`` (DECISIONS
    2026-05-23); when TCGCSV reconciliation corrects that rarity in place, the
    original ``(set_code, set_rarity)`` no longer matches on a re-sync, so the
    YGOPRODeck sync would re-create the provisional row as a duplicate. This alias
    records the original key → canonical printing so the re-sync resolves to the
    canonical row instead — the round-4 rerun-safety prerequisite, i.e. the
    ``external_price_ids`` pattern applied to metadata identity.

    Keyed ``(source, card, set_code, set_rarity)`` where ``set_rarity`` is the
    *provisional* value. All columns non-null, so a plain UNIQUE exercised on
    sqlite too (unlike the ``CardPrinting`` natural key). ``card`` is denormalized
    from the resolved printing for an explicit, self-describing key; it always
    equals ``printing.card``. ``variant_label`` is intentionally absent: v1 only
    aliases the in-place rarity-correction case (multi-variant splits go to the
    review queue, never aliased), so the provisional key's variant is always NULL.
    It joins this key via an additive migration if variant-splitting ever lands.
    """

    source = models.CharField(max_length=32, choices=MetadataSource.choices)
    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name="printing_aliases")
    set_code = models.CharField(max_length=32, db_index=True)
    set_rarity = models.CharField(max_length=64)
    # The canonical printing the provisional key now resolves to. CASCADE: the
    # alias is a re-derivable leaf (like external_price_ids), meaningless without
    # its printing, so it should vanish with it rather than block the delete.
    printing = models.ForeignKey(CardPrinting, on_delete=models.CASCADE, related_name="aliases")

    class Meta:
        ordering = ["source", "set_code", "set_rarity"]
        constraints = [
            # One alias per (source, card, set_code, provisional rarity). All-non-null
            # → a plain UNIQUE created and exercised on sqlite too.
            models.UniqueConstraint(
                fields=["source", "card", "set_code", "set_rarity"],
                name="unique_printing_alias_provisional_key",
            ),
            # Closed vocabulary; `choices` is form-layer only, so guard the column at
            # the DB on every backend (the PriceSnapshot/CollectionItem enum precedent).
            models.CheckConstraint(
                condition=models.Q(source__in=MetadataSource.values),
                name="printing_alias_source_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_source_display()} {self.set_code}/{self.set_rarity} -> {self.printing}"
