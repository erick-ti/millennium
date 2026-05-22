from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


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

    class Provider(models.TextChoices):
        TCGCSV = "tcgcsv", "TCGCSV"

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
