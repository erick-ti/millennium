from __future__ import annotations

from django.db import models

from apps.core.enums import Edition
from apps.core.models import TimeStampedModel


class Provider(models.TextChoices):
    """Pricing data providers, shared by ``ExternalPriceId`` (which provider knows
    a printing by which id) and ``PriceSnapshot`` (which provider a price came
    from). TCGCSV is the only source for Phase 1B; YGOPRODeck is metadata-only.
    """

    TCGCSV = "tcgcsv", "TCGCSV"


class ExternalPriceId(TimeStampedModel):
    """A pricing provider's own identifier for a ``CardPrinting``.

    Printing identity is provider-agnostic: a provider's
    product id lives here as ``(printing, provider, external_id)`` rather than as
    a column on ``card_printings``, so adding a second provider is an INSERT, not
    a migration. TCGCSV's ``productId`` is the only provider for Phase 1B.

    A single printing may map to several ids for the *same* provider over time
    (e.g. a provider-side re-classification keeps the old id resolvable while a new
    one becomes canonical), so ``(printing, provider)`` is indexed but not unique.
    Uniqueness is on ``(provider, external_id)``: a given provider id resolves to
    exactly one printing.
    """

    # CASCADE, unlike CardPrinting.card (PROTECT): an external id is a pure
    # provider mapping with no independent value and nothing referencing it, and
    # is re-derivable from a provider sync, so it should vanish with its printing
    # rather than block the delete. db_index=False because the (printing, provider)
    # index below already covers printing-keyed lookups; a lone FK index is redundant.
    printing = models.ForeignKey(
        "cards.CardPrinting",
        on_delete=models.CASCADE,
        related_name="external_price_ids",
        db_index=False,
    )
    provider = models.CharField(max_length=32, choices=Provider.choices)
    # Opaque provider identifier, kept as text rather than int: providers don't
    # agree on id format (TCGCSV's productId is numeric, others use alphanumeric/
    # UUID) and the value is never arithmetic. For TCGCSV this is the productId.
    external_id = models.CharField(max_length=64)

    class Meta:
        ordering = ["provider", "external_id"]
        constraints = [
            # A provider id maps to one printing, the same (provider, external_id)
            # can't be claimed twice. A plain UNIQUE (no NULL semantics), so unlike
            # the CardPrinting natural key it IS created and exercised on sqlite too.
            models.UniqueConstraint(
                fields=["provider", "external_id"],
                name="unique_external_price_id_per_provider",
            ),
        ]
        indexes = [
            # Covering index for the hot "what does <provider> call this printing?"
            # lookup; its leftmost prefix also serves the FK.
            models.Index(fields=["printing", "provider"], name="epi_printing_provider_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_provider_display()}:{self.external_id}"


class PriceSnapshot(TimeStampedModel):
    """One provider's price for a printing+edition on a given day, append-only
    daily history.

    Pricing refreshes on a daily schedule and historical analytics is a core
    feature, so snapshots are inserted and never updated: "today's price" is the
    latest snapshot per ``(printing, edition, source)`` and a price series is a
    range scan, which makes re-running an ingestion idempotent. (Append-only is a
    convention here, not a ``save()``-enforced lock.)

    Edition is a pricing dimension: TCGCSV prices the same
    product differently per ``subTypeName`` (1st Edition vs Unlimited), so a
    printing has one snapshot *per edition* per source per day. ``source`` is the
    shared ``Provider`` enum rather than a ``price_sources`` FK table (resolved:
    one source today, and per-source config can become a Phase 2 table keyed by
    this slug without rewriting history).
    ``source_subtype_name`` keeps the raw provider subtype (e.g. TCGCSV "1st
    Edition") for audit if the edition-normalisation rule ever changes.

    The ``printing`` FK is ``PROTECT``: a price *series* is not re-derivable (you
    can't re-fetch a past day's price) and losing it would gut the historical
    analytics, so a stray printing delete must not cascade it away, unlike the
    re-derivable leaf ``ExternalPriceId`` (CASCADE).
    """

    printing = models.ForeignKey(
        "cards.CardPrinting", on_delete=models.PROTECT, related_name="price_snapshots"
    )
    edition = models.CharField(max_length=16, choices=Edition.choices)
    source = models.CharField(max_length=32, choices=Provider.choices)
    snapshot_date = models.DateField()
    # Every price point is nullable, a provider may report only some of them.
    # Decimal (never float) for money; 2 dp matches TCGCSV's USD cents.
    low_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    mid_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    high_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    market_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    direct_low_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # Multi-source confidence score. One trusted source
    # today, so 1.0; the scoring semantics land in Phase 2, hence no range CHECK yet.
    confidence = models.FloatField(default=1.0)
    # Raw provider subtype the edition was normalised from (e.g. "1st Edition"),
    # kept for audit. Open text, so no CHECK (the set_code precedent).
    source_subtype_name = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001

    class Meta:
        # Latest-first within a printing+edition. The four ordering fields are
        # exactly the natural key (all non-null), so the order is fully
        # deterministic with no separate tiebreaker needed.
        ordering = ["printing", "edition", "-snapshot_date", "source"]
        constraints = [
            # Append-only daily key: one row per (printing, edition, source, day).
            # All columns non-null → a plain UNIQUE, created and exercised on sqlite
            # too (like the CollectionItem key, unlike the CardPrinting one).
            models.UniqueConstraint(
                fields=["printing", "edition", "source", "snapshot_date"],
                name="unique_price_snapshot_natural_key",
            ),
            # Closed-vocabulary guards: `choices` is form-layer only, so .create() /
            # bulk paths could otherwise persist an out-of-vocabulary value and split
            # the key. Enforced on every backend, like the CollectionItem enum CHECKs.
            models.CheckConstraint(
                condition=models.Q(edition__in=Edition.values),
                name="price_snapshot_edition_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(source__in=Provider.values),
                name="price_snapshot_source_valid",
            ),
            # Money can't be negative; each price point is NULL or >= 0. NaN/inf are
            # already blocked upstream (the TCGCSV `_to_decimal` boundary guard and
            # Django's DecimalField quantize), so this backstops a negative from any
            # source (a manual admin edit, a future provider), matching the
            # CollectionLot / PortfolioValueSnapshot money-CHECK pattern.
            models.CheckConstraint(
                condition=(models.Q(low_price__isnull=True) | models.Q(low_price__gte=0))
                & (models.Q(mid_price__isnull=True) | models.Q(mid_price__gte=0))
                & (models.Q(high_price__isnull=True) | models.Q(high_price__gte=0))
                & (models.Q(market_price__isnull=True) | models.Q(market_price__gte=0))
                & (models.Q(direct_low_price__isnull=True) | models.Q(direct_low_price__gte=0)),
                name="price_snapshot_prices_non_negative",
            ),
        ]
        indexes = [
            # Covering index for the hot "latest price for this printing+edition"
            # lookup: leftmost prefix + descending date.
            models.Index(
                fields=["printing", "edition", "-snapshot_date"],
                name="price_snapshot_latest_idx",
            ),
            # snapshot_date-leading companion for the date-anchored scan that the
            # latest-price-map issues across MANY printings at once (Phase 5): both
            # ``value_all_portfolios`` (catalog-wide, every day) and the collection-
            # scoped "biggest movers" query filter ``source=TCGCSV`` + a
            # ``snapshot_date <= anchor`` bound and then pick each (printing, edition)
            # group's max date. The printing-leading index above serves the per-pair
            # correlated subquery; this one serves the outer source+date scan that
            # crosses printings, which a printing-leading index can't drive.
            models.Index(
                fields=["source", "-snapshot_date", "printing", "edition"],
                name="price_snapshot_movers_idx",
            ),
        ]

    def __str__(self) -> str:
        price = "no price" if self.market_price is None else f"market {self.market_price}"
        return (
            f"{self.printing} ({self.get_edition_display()}) "
            f"{self.get_source_display()} {self.snapshot_date}: {price}"
        )


class UnmatchedReason(models.TextChoices):
    """Why a provider product couldn't be auto-resolved to a single printing."""

    NO_PRINTING_MATCH = "no_printing_match", "No printing match"
    MULTI_VARIANT = "multi_variant", "Multiple variants"
    RARITY_DISAGREEMENT = "rarity_disagreement", "Rarity disagreement"
    # The product's id already resolves to a *different* printing (provider-side
    # drift across runs, a manual edit, or a prior bad run). (provider, external_id)
    # is unique and which side is correct needs a human, so we queue rather than
    # silently rewrite the mapping or report a false match.
    EXTERNAL_ID_CONFLICT = "external_id_conflict", "External id conflict"


class UnmatchedStatus(models.TextChoices):
    """Human triage state for a review-queue entry."""

    UNRESOLVED = "unresolved", "Unresolved"
    RESOLVED = "resolved", "Resolved"
    IGNORED = "ignored", "Ignored"


class UnmatchedProduct(TimeStampedModel):
    """A pricing-provider product the reconciliation could not safely resolve to
    exactly one ``CardPrinting``, the review queue (unresolved conflicts are
    queued, never silently guessed). Mutable: a human
    triages ``status``.

    Upserted on ``(provider, external_id)`` so a daily re-run refreshes an entry
    rather than piling up duplicates, while preserving a human's ``status`` /
    ``notes``. ``product_name`` keeps the raw provider name (its parenthetical is
    the variant signal a human needs); ``reason`` distinguishes the failure class.
    Not append-only history (unlike ``PriceSnapshot``), it's a work list, so it
    carries a normal admin with no delete/edit lockdown.
    """

    provider = models.CharField(max_length=32, choices=Provider.choices)
    external_id = models.CharField(max_length=64)
    set_code = models.CharField(max_length=32, db_index=True)
    set_rarity = models.CharField(max_length=64)
    product_name = models.CharField(max_length=255)
    set_name = models.CharField(max_length=255, blank=True, default="")
    reason = models.CharField(max_length=32, choices=UnmatchedReason.choices)
    status = models.CharField(
        max_length=16, choices=UnmatchedStatus.choices, default=UnmatchedStatus.UNRESOLVED
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["provider", "status", "set_code", "set_rarity"]
        constraints = [
            # One queue entry per provider product; a re-run upserts on this key.
            # Both columns non-null → a plain UNIQUE exercised on sqlite too.
            models.UniqueConstraint(
                fields=["provider", "external_id"],
                name="unique_unmatched_product_per_provider",
            ),
            # Closed-vocabulary guards (the PriceSnapshot/CollectionItem precedent):
            # `choices` is form-layer only, so guard each enum at the DB everywhere.
            models.CheckConstraint(
                condition=models.Q(provider__in=Provider.values),
                name="unmatched_product_provider_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(reason__in=UnmatchedReason.values),
                name="unmatched_product_reason_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=UnmatchedStatus.values),
                name="unmatched_product_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_reason_display()}: {self.set_code}/{self.set_rarity} ({self.external_id})"
