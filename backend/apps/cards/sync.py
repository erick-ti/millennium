from __future__ import annotations

from dataclasses import dataclass

from apps.cards.models import Card, CardPrinting
from apps.pricing.providers.base import MetadataProvider


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Per-run counts from a metadata sync."""

    cards_created: int = 0
    cards_updated: int = 0
    cards_unchanged: int = 0
    printings_created: int = 0
    printings_updated: int = 0
    printings_unchanged: int = 0


def sync_cards_from_metadata(provider: MetadataProvider) -> SyncResult:
    """Upsert cards and printings from a metadata provider, skipping unchanged rows.

    An existing row is written *only* when a field actually differs from the
    provider's value — an unchanged row is left untouched, so ``updated_at`` stays
    meaningful (it marks the last real metadata change, not the last sync) and the
    returned counts are a true change signal rather than "everything, every run"
    (which is what an unconditional ``update_or_create`` would report). Writes go
    through ``save`` (never ``bulk_create``), which is what derives
    ``normalized_name`` and coerces ``variant_label`` (DECISIONS 2026-05-20), so a
    re-run over unchanged data performs no writes at all. Single-writer: the daily
    sync is one task, so get-then-write needs no row locking.

    SCOPE — initial / manual seed only, NOT yet safe to wire recurring. The
    printing upsert keys on ``(card, set_code, set_rarity, variant_label)``, but
    DECISIONS 2026-05-23 makes the YGOPRODeck rarity/variant *provisional*: TCGCSV
    ingestion later mutates those key columns (rarity correction in place, variant
    splitting). A re-run after that recomputes the *original* provisional key,
    misses the canonicalized row, and re-creates the provisional duplicate. Before
    this is scheduled (slice 4) or runs alongside TCGCSV canonicalization (slice
    3) it must become reconciliation-aware via a stable provider-side alias
    (DECISIONS 2026-05-23 round-4 follow-up).
    """
    cards_created = cards_updated = cards_unchanged = 0
    printings_created = printings_updated = printings_unchanged = 0
    for record in provider.fetch_card_metadata():
        try:
            card = Card.objects.get(passcode=record.passcode)
        except Card.DoesNotExist:
            card = Card.objects.create(passcode=record.passcode, name=record.name)
            cards_created += 1
        else:
            if card.name != record.name:
                card.name = record.name
                card.save()  # full save: derives normalized_name, bumps updated_at
                cards_updated += 1
            else:
                cards_unchanged += 1
        for printing in record.printings:
            try:
                existing = CardPrinting.objects.get(
                    card=card,
                    set_code=printing.set_code,
                    set_rarity=printing.set_rarity,
                    variant_label=printing.variant_label,
                )
            except CardPrinting.DoesNotExist:
                CardPrinting.objects.create(
                    card=card,
                    set_code=printing.set_code,
                    set_rarity=printing.set_rarity,
                    variant_label=printing.variant_label,
                    set_name=printing.set_name,
                )
                printings_created += 1
            else:
                # set_name is the only mutable (non-natural-key) field a metadata
                # refresh can change for an existing printing.
                if existing.set_name != printing.set_name:
                    existing.set_name = printing.set_name
                    existing.save()
                    printings_updated += 1
                else:
                    printings_unchanged += 1
    return SyncResult(
        cards_created=cards_created,
        cards_updated=cards_updated,
        cards_unchanged=cards_unchanged,
        printings_created=printings_created,
        printings_updated=printings_updated,
        printings_unchanged=printings_unchanged,
    )
