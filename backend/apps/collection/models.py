from __future__ import annotations

from django.db import models

from apps.core.enums import Edition
from apps.core.models import TimeStampedModel


class Condition(models.TextChoices):
    """Card condition, in Dragon Shield's vocabulary (the import source).

    Stored faithfully as DS reports it rather than collapsed into TCGplayer's
    NM/LP/MP/HP/DMG pricing scale (that DS to TCGplayer mapping for condition
    price adjustment is a separate Phase 2 concern). v1: only ``NearMint`` was
    seen in the recon sample; the full set is per DS docs, to be confirmed in
    Phase 3.
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
    "EN") structurally. v1 set: the six TCG languages plus Japanese/Korean;
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
    """A physical place a collection is kept, e.g. "Binder A page 3",
    "Deck box #2", "Safe deposit box".

    Distinct from ``Portfolio``, which is a *logical* grouping: a holding's
    portfolio and its physical location are two independent dimensions.
    Unlike a portfolio, a storage location is NOT find-or-created from the
    Dragon Shield import: the user creates it and assigns it manually, and
    ``collection_items`` reference it via a *nullable*
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
    """An aggregate owned holding: N copies of one printing in one condition,
    edition, language, and portfolio.

    The natural key is ``(printing, condition, edition, language, portfolio)``.
    All five are non-null, so the ``UniqueConstraint`` is a plain UNIQUE that is
    created and exercised on sqlite too (unlike the ``CardPrinting`` natural
    key). "3 Ash Blossom 1st-Edition NM English in the Yubel Deck" is one row;
    the quantity and the per-acquisition cost basis live on child
    ``collection_lots`` (next model), so quantity is derived (SUM of lots), not
    stored here. ``storage_location`` is a *single* nullable physical-whereabouts
    annotation (``SET_NULL``, the holding survives if a location is deleted) and
    is deliberately NOT part of the natural key: a holding records one location,
    so splitting N copies across locations (per-copy / binder-slot placement) is
    out of scope this phase, deferred to a future
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
            # (sqlite included), the structural guarantee the enum was chosen
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


class CollectionLot(TimeStampedModel):
    """A single acquisition batch under a ``CollectionItem``.

    The ``CollectionItem`` is the aggregate holding ("3 Ash Blossom 1st-Edition
    NM English in the Yubel Deck"); each lot is one acquisition event that added
    copies to it, carrying that event's cost basis. A holding's quantity is the
    SUM of its lots' ``quantity`` (it is NOT stored on the item), and
    per-acquisition P&L (FIFO / LIFO / average cost) operates on lots, the
    textbook cost-basis primitive.

    Edition is deliberately NOT stored here: it is inherited from the parent
    item, so a lot can't drift to a different edition than the
    holding it belongs to. Lots are conceptually immutable acquisition records,
    but that is a convention (correcting a mistaken cost must stay possible), not
    a ``save()``-enforced lock.

    The ``collection_item`` FK is ``CASCADE``: a lot is *part of* its holding, so
    deleting the holding takes its acquisition events with it. This differs from
    the leaf-mapping CASCADE on ``ExternalPriceId`` (cost basis is not
    re-derivable), but the valuable data is still shielded from accidental loss
    by the ``PROTECT`` FKs one level up: nothing cascades *into* a
    ``CollectionItem`` (its ``printing``/``portfolio`` are PROTECT,
    ``storage_location`` is SET_NULL), so the only path here is a deliberate
    holding delete, where dropping its lots is the correct outcome.
    """

    collection_item = models.ForeignKey(
        CollectionItem, on_delete=models.CASCADE, related_name="lots"
    )
    quantity = models.PositiveIntegerField()
    # Per-card acquisition cost, named unit_cost.
    # Decimal, never float, for money. Dragon Shield's "Price Bought" is itself
    # per-card, so the Phase 3 import maps it
    # straight to unit_cost with no total/quantity division: a per-card USD price
    # is cents, represented exactly at 2 dp. Nullable: an acquisition's price can be
    # genuinely unknown (pack pulls, trades, gifts, legacy hand-entry), and NULL
    # ("unknown") is kept distinct from 0.00 ("free") so unknown cost can't
    # masquerade as zero basis and inflate P&L.
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # Acquisition date (DS "Date Bought" is a date, not a timestamp). Nullable for
    # the same unknown-provenance reason as unit_cost; not part of any identity.
    acquired_at = models.DateField(null=True, blank=True)
    # Back-reference to the import that created this lot, for traceability AND
    # re-import dedup. The Phase 3 DS importer writes a per-holding-per-source key
    # ("dragon_shield:item:<id>") so re-importing a full-collection snapshot is
    # idempotent: one import-sourced lot per holding.
    # Nullable: manual (non-import) lots have none, and a holding may legitimately
    # have several manual acquisition lots, so the uniqueness below is scoped to
    # NON-NULL refs (NULLs stay distinct, the default), enforcing dedup only on
    # import-sourced lots. Trimming/validating the ref is the import boundary's job
    # (the external_id precedent); the importer constructs it, so it is always clean.
    import_source_ref = models.CharField(max_length=255, null=True, blank=True)  # noqa: DJ001

    class Meta:
        # Deterministic, backend-portable order: chronological by acquisition, with
        # unknown-date lots explicitly last (nulls_last makes sqlite, which sorts
        # NULLs first by default, agree with Postgres, which sorts them last), then
        # id as a stable tiebreaker so same-date lots have a defined order. FIFO/LIFO
        # cost-basis code must still set its own explicit order_by, not lean on this.
        ordering = ["collection_item", models.F("acquired_at").asc(nulls_last=True), "id"]
        constraints = [
            # A lot of zero or negative copies is meaningless. PositiveIntegerField
            # only adds a form-layer validator (MinValueValidator), not a DB guard,
            # so an explicit CHECK > 0 is what actually rejects 0 / negatives on
            # every backend (sqlite included, so `make test` exercises it), the
            # project's "guard at the DB, not just the field/form layer" pattern.
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="collection_lot_quantity_positive",
            ),
            # Cost basis may be unknown (NULL) or zero (free) but never negative.
            models.CheckConstraint(
                condition=models.Q(unit_cost__isnull=True) | models.Q(unit_cost__gte=0),
                name="collection_lot_unit_cost_non_negative",
            ),
            # Per-holding-per-source import dedup: at most one lot per
            # (collection_item, import_source_ref). import_source_ref is nullable, and
            # SQL's default NULLS DISTINCT (no nulls_distinct=False here) lets a holding
            # keep many manual lots (ref NULL, all distinct) while allowing only one lot
            # per concrete import key, so re-importing a DS snapshot find-or-creates the
            # same lot instead of duplicating it. A plain UNIQUE over a nullable column is
            # created on every backend (sqlite included, unlike the NULLS-NOT-DISTINCT
            # CardPrinting key), so `make test` exercises it. This is the DB backstop the
            # importer's get_or_create relies on to be race-safe; it discharges the
            # re-import dedup obligation of ensuring a re-imported snapshot never
            # duplicates or overwrites existing acquisition history.
            models.UniqueConstraint(
                fields=["collection_item", "import_source_ref"],
                name="unique_lot_per_collection_item_import_source_ref",
            ),
        ]

    def __str__(self) -> str:
        cost = "cost unknown" if self.unit_cost is None else f"{self.unit_cost} each"
        return f"{self.quantity} x {self.collection_item} ({cost})"
