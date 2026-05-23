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

    Printing identity is provider-agnostic (DECISIONS 2026-05-18): a provider's
    product id lives here as ``(printing, provider, external_id)`` rather than as
    a column on ``card_printings``, so adding a second provider is an INSERT, not
    a migration. TCGCSV's ``productId`` is the only provider for Phase 1B.

    A single printing may map to several ids for the *same* provider over time —
    e.g. a provider-side re-classification keeps the old id resolvable while a new
    one becomes canonical — so ``(printing, provider)`` is indexed but not unique.
    Uniqueness is on ``(provider, external_id)``: a given provider id resolves to
    exactly one printing.
    """

    # CASCADE, unlike CardPrinting.card (PROTECT): an external id is a pure
    # provider mapping with no independent value and nothing referencing it, and
    # is re-derivable from a provider sync — so it should vanish with its printing
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
            # A provider id maps to one printing — the same (provider, external_id)
            # can't be claimed twice. A plain UNIQUE (no NULL semantics), so unlike
            # the CardPrinting natural key it IS created and exercised on sqlite too.
            models.UniqueConstraint(
                fields=["provider", "external_id"],
                name="unique_external_price_id_per_provider",
            ),
        ]
        indexes = [
            # Covering index for the hot "what does <provider> call this printing?"
            # lookup (DECISIONS 2026-05-18); its leftmost prefix also serves the FK.
            models.Index(fields=["printing", "provider"], name="epi_printing_provider_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_provider_display()}:{self.external_id}"


class PriceSnapshot(TimeStampedModel):
    """One provider's price for a printing+edition on a given day — append-only
    daily history (DECISIONS 2026-05-18).

    Pricing refreshes on a daily schedule and historical analytics is a core
    feature, so snapshots are inserted and never updated: "today's price" is the
    latest snapshot per ``(printing, edition, source)`` and a price series is a
    range scan, which makes re-running an ingestion idempotent. (Append-only is a
    convention here, not a ``save()``-enforced lock.)

    Edition is a pricing dimension (DECISIONS 2026-05-18): TCGCSV prices the same
    product differently per ``subTypeName`` (1st Edition vs Unlimited), so a
    printing has one snapshot *per edition* per source per day. ``source`` is the
    shared ``Provider`` enum rather than a ``price_sources`` FK table (Open
    Question #1, resolved 2026-05-22: one source today, and per-source config can
    become a Phase 2 table keyed by this slug without rewriting history).
    ``source_subtype_name`` keeps the raw provider subtype (e.g. TCGCSV "1st
    Edition") for audit if the edition-normalisation rule ever changes.

    The ``printing`` FK is ``PROTECT``: a price *series* is not re-derivable (you
    can't re-fetch a past day's price) and losing it would gut the historical
    analytics, so a stray printing delete must not cascade it away — unlike the
    re-derivable leaf ``ExternalPriceId`` (CASCADE).
    """

    printing = models.ForeignKey(
        "cards.CardPrinting", on_delete=models.PROTECT, related_name="price_snapshots"
    )
    edition = models.CharField(max_length=16, choices=Edition.choices)
    source = models.CharField(max_length=32, choices=Provider.choices)
    snapshot_date = models.DateField()
    # Every price point is nullable — a provider may report only some of them.
    # Decimal (never float) for money; 2 dp matches TCGCSV's USD cents.
    low_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    mid_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    high_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    market_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    direct_low_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # Multi-source confidence score (DECISIONS 2026-05-18). One trusted source
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
        ]
        indexes = [
            # Covering index for the hot "latest price for this printing+edition"
            # lookup (DECISIONS 2026-05-18): leftmost prefix + descending date.
            models.Index(
                fields=["printing", "edition", "-snapshot_date"],
                name="price_snapshot_latest_idx",
            ),
        ]

    def __str__(self) -> str:
        price = "no price" if self.market_price is None else f"market {self.market_price}"
        return (
            f"{self.printing} ({self.get_edition_display()}) "
            f"{self.get_source_display()} {self.snapshot_date}: {price}"
        )
