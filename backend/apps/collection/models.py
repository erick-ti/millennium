from __future__ import annotations

from django.db import models

from apps.core.enums import Edition
from apps.core.models import TimeStampedModel


class Condition(models.TextChoices):
    """Card condition, in Dragon Shield's vocabulary (the import source).

    Stored faithfully as DS reports it rather than collapsed into TCGplayer's
    NM/LP/MP/HP/DMG pricing scale — that DS→TCGplayer mapping for condition
    price adjustment is a separate Phase 2 concern. v1: only ``NearMint`` was
    seen in the recon sample; the full set is per DS docs, to be confirmed in
    Phase 3 (see PHASE_1A5_FINDINGS "DS condition vocabulary completeness").
    """

    MINT = "mint", "Mint"
    NEAR_MINT = "near_mint", "Near Mint"
    EXCELLENT = "excellent", "Excellent"
    GOOD = "good", "Good"
    LIGHT_PLAYED = "light_played", "Light Played"
    PLAYED = "played", "Played"
    POOR = "poor", "Poor"


class Language(models.TextChoices):
    """Print language, stored as an ISO 639-1 code.

    A closed vocabulary, so an enum (rather than free text) keeps the
    ``collection_items`` natural key free of dirty aliases ("English"/"english"/
    "EN") structurally. v1 set — the six TCG languages plus Japanese/Korean;
    DS exports full names ("English"), mapped to the code at the import boundary.
    """

    ENGLISH = "en", "English"
    FRENCH = "fr", "French"
    GERMAN = "de", "German"
    ITALIAN = "it", "Italian"
    SPANISH = "es", "Spanish"
    PORTUGUESE = "pt", "Portuguese"
    JAPANESE = "ja", "Japanese"
    KOREAN = "ko", "Korean"


class StorageLocation(TimeStampedModel):
    """A physical place a collection is kept — e.g. "Binder A page 3",
    "Deck box #2", "Safe deposit box".

    Distinct from ``Portfolio``, which is a *logical* grouping (DECISIONS
    2026-05-18): a holding's portfolio and its physical location are two
    independent dimensions. Unlike a portfolio, a storage location is NOT
    find-or-created from the Dragon Shield import — the user creates it and
    assigns it manually, and ``collection_items`` reference it via a *nullable*
    FK. ``name`` is unique to prevent duplicate physical-location entries and to
    give that FK a clean autocomplete target; there is no normalized form
    because nothing matches it against import text.
    """

    name = models.CharField(max_length=255, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class CollectionItem(TimeStampedModel):
    """An aggregate owned holding — N copies of one printing in one condition,
    edition, language, and portfolio (DECISIONS 2026-05-18).

    The natural key is ``(printing, condition, edition, language, portfolio)``.
    All five are non-null, so the ``UniqueConstraint`` is a plain UNIQUE that is
    created and exercised on sqlite too (unlike the ``CardPrinting`` natural
    key). "3 Ash Blossom 1st-Edition NM English in the Yubel Deck" is one row;
    the quantity and the per-acquisition cost basis live on child
    ``collection_lots`` (next model), so quantity is derived (SUM of lots), not
    stored here. ``storage_location`` is a *single* nullable physical-whereabouts
    annotation (``SET_NULL`` — the holding survives if a location is deleted) and
    is deliberately NOT part of the natural key: a holding records one location,
    so splitting N copies across locations (per-copy / binder-slot placement) is
    out of scope this phase (DECISIONS 2026-05-18) — a future
    ``(item, location, quantity)`` allocation layer if real use ever needs it,
    not now. The ``printing`` and ``portfolio`` FKs are ``PROTECT`` so deleting
    either can't silently destroy a holding and its cost basis.
    """

    printing = models.ForeignKey(
        "cards.CardPrinting", on_delete=models.PROTECT, related_name="collection_items"
    )
    portfolio = models.ForeignKey(
        "portfolio.Portfolio", on_delete=models.PROTECT, related_name="collection_items"
    )
    storage_location = models.ForeignKey(
        StorageLocation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="collection_items",
    )
    condition = models.CharField(max_length=16, choices=Condition.choices)
    edition = models.CharField(max_length=16, choices=Edition.choices)
    language = models.CharField(max_length=8, choices=Language.choices)

    class Meta:
        ordering = ["portfolio", "printing"]
        constraints = [
            # Natural key. All columns non-null → a plain UNIQUE, created and
            # exercised on sqlite (no NULLS NOT DISTINCT / Postgres-only gap like
            # the CardPrinting key). storage_location is deliberately NOT part of
            # the key: a holding has one location (or none), not an identity per
            # location.
            models.UniqueConstraint(
                fields=["printing", "condition", "edition", "language", "portfolio"],
                name="unique_collection_item_natural_key",
            ),
            # Closed-vocabulary guards. `choices` only validates at the form /
            # full_clean() layer; .create() / bulk_create / QuerySet.update / raw
            # SQL bypass it, and since the natural key compares raw column values
            # an out-of-vocabulary alias ("NearMint", "1st Edition", "English")
            # would persist as a *distinct* holding for the same physical card.
            # A CHECK enforces the closed set at the DB layer on every backend
            # (sqlite included) — the structural guarantee the enum was chosen
            # for. Distinct from the set_code/external_id deferral: those are
            # open text with no finite domain to CHECK.
            models.CheckConstraint(
                condition=models.Q(condition__in=Condition.values),
                name="collection_item_condition_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(edition__in=Edition.values),
                name="collection_item_edition_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(language__in=Language.values),
                name="collection_item_language_valid",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.printing} ({self.get_edition_display()}, "
            f"{self.get_condition_display()}, {self.get_language_display()}) in {self.portfolio}"
        )
