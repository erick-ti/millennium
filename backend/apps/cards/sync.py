from __future__ import annotations

from dataclasses import asdict, dataclass

import structlog
from django.conf import settings

from apps.cards.models import Card, CardPrinting, MetadataSource, PrintingAlias
from apps.core.locks import sync_lock
from apps.core.models import SyncKind, SyncStatus
from apps.core.sync_history import record_run, shrink_floor
from apps.pricing.providers.base import JsonFetcher, MetadataProvider, fetch_json
from apps.pricing.providers.ygoprodeck import YgoprodeckProvider

logger = structlog.get_logger(__name__)

# A single run may legitimately null archetype on a handful of cards (Konami errata /
# YGOPRODeck corrections), and an early/small catalog shouldn't fail the whole metadata
# sync over a fractional threshold. Only ABOVE this many archetype withdrawals does the
# fraction guard engage; below it, withdrawals are always allowed through.
_ARCHETYPE_WITHDRAWAL_FLOOR = 25


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Per-run counts from a metadata sync."""

    cards_created: int = 0
    cards_updated: int = 0
    cards_unchanged: int = 0
    printings_created: int = 0
    printings_updated: int = 0
    printings_unchanged: int = 0
    # Non-null archetypes in this run's fetch: coverage telemetry recorded in the
    # SyncRun detail (Phase 5). NOT the guard input: the withdrawal guard reads the live
    # tagged set, not a recorded baseline.
    archetype_count: int = 0


def sync_cards_from_metadata(
    provider: MetadataProvider, *, archetype_withdrawal_tolerance: float | None = None
) -> SyncResult:
    """Upsert cards and printings from a metadata provider, skipping unchanged rows.

    An existing row is written *only* when a field actually differs from the
    provider's value, an unchanged row is left untouched, so ``updated_at`` stays
    meaningful (it marks the last real metadata change, not the last sync) and the
    returned counts are a true change signal rather than "everything, every run"
    (which is what an unconditional ``update_or_create`` would report). Writes go
    through ``save`` (never ``bulk_create``), which is what derives
    ``normalized_name`` and coerces ``variant_label``, so a
    re-run over unchanged data performs no writes at all. Single-writer: the daily
    sync is one task, so get-then-write needs no row locking.

    Reconciliation-aware: before matching a printing
    by its natural key it consults ``PrintingAlias``, so once TCGCSV ingestion has
    corrected a provisional ``set_rarity`` in place, a re-run resolves the original
    provisional key to the canonical printing instead of recreating it as a
    duplicate. (Beat scheduling still waits on the second recurring-safety
    prerequisite: the compare-to-previous cardinality guard.)
    """
    cards_created = cards_updated = cards_unchanged = 0
    printings_created = printings_updated = printings_unchanged = 0
    # Materialize before writing so the archetype-withdrawal guard can fail closed on a
    # field-degraded fetch BEFORE any card is overwritten. archetype is OPTIONAL upstream,
    # so a record with archetype=None reads as a withdrawal and would NULL an existing
    # tag. A full OR PARTIAL key-drop (a subset of cards) would silently wipe those tags
    # while passing the card-count floor (which counts cards, not archetypes). So guard
    # the destructive op directly: count how many CURRENTLY-tagged cards this run would
    # null, and fail closed if that exceeds a fraction of the tagged set (above a small
    # absolute floor, so early/small states and legit handful-of-card corrections don't
    # trip it). An aggregate count floor would let a partial loss slip through.
    records = list(provider.fetch_card_metadata())
    archetype_count = sum(1 for record in records if record.archetype is not None)
    if archetype_withdrawal_tolerance is not None:
        tagged = set(
            Card.objects.exclude(archetype__isnull=True).values_list("passcode", flat=True)
        )
        withdrawals = sum(
            1 for record in records if record.archetype is None and record.passcode in tagged
        )
        threshold = max(
            _ARCHETYPE_WITHDRAWAL_FLOOR, int(len(tagged) * archetype_withdrawal_tolerance)
        )
        if withdrawals > threshold:
            raise ValueError(
                f"sync would null archetype on {withdrawals} of {len(tagged)} currently "
                f"tagged cards (> threshold {threshold}) -- refusing a likely "
                f"field-degraded dump that would wipe existing archetype tags."
            )
    for record in records:
        try:
            card = Card.objects.get(passcode=record.passcode)
        except Card.DoesNotExist:
            card = Card.objects.create(
                passcode=record.passcode, name=record.name, archetype=record.archetype
            )
            cards_created += 1
        else:
            # name and archetype are the mutable provider-supplied fields; an
            # archetype change (added, corrected, or withdrawn → None) is a real
            # metadata update, so it counts and writes like a rename does.
            if card.name != record.name or card.archetype != record.archetype:
                card.name = record.name
                card.archetype = record.archetype
                card.save()  # full save: derives normalized_name, bumps updated_at
                cards_updated += 1
            else:
                cards_unchanged += 1
        for printing in record.printings:
            alias = PrintingAlias.objects.filter(
                source=MetadataSource.YGOPRODECK,
                card=card,
                set_code=printing.set_code,
                set_rarity=printing.set_rarity,
            ).first()
            if alias is not None:
                # A prior TCGCSV reconciliation corrected this provisional
                # (set_code, set_rarity) to a canonical printing; resolve to that row
                # rather than recreating the provisional one.
                # Treat like an existing printing, refresh set_name if it
                # drifted (the only mutable, non-key field a refresh changes).
                canonical = alias.printing
                if canonical.set_name != printing.set_name:
                    canonical.set_name = printing.set_name
                    canonical.save()
                    printings_updated += 1
                else:
                    printings_unchanged += 1
                continue
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
        archetype_count=archetype_count,
    )


def run_ygoprodeck_sync(*, fetch: JsonFetcher = fetch_json) -> SyncResult | None:
    """Run the YGOPRODeck metadata sync under the compare-to-previous cardinality
    guard, recording the outcome in ``SyncRun``.

    The recurring-safety guard (prerequisite #2): once a prior successful run
    exists, the provider's fetch floor is raised to ``last_good_cards * (1 - tolerance)``,
    so a truncated bulk dump is rejected *before* any write; the first run has no
    history, so the provider's own absolute bootstrap floor applies. A SUCCESS row
    records the run's cardinality (the next run's baseline); a failure (including a
    guard rejection or a misconfigured tolerance) records FAILED + the error and
    re-raises, so a bad fetch never becomes the baseline.

    Serialized by a per-kind advisory lock: the underlying upsert paths assume a single
    writer, which beat alone doesn't enforce (e.g. a manual ``sync_ygoprodeck`` overlapping
    the scheduled task). If another run already holds the lock this one **skips** (logs and
    returns ``None`` -- no ``SyncRun``, since it never ran), rather than racing the
    get-then-create paths. The single entry point for the sync, called by both the
    management command and the Celery task. ``fetch`` is injectable so tests can drive it
    without the network.
    """
    with sync_lock(SyncKind.YGOPRODECK_METADATA) as acquired:
        if not acquired:
            logger.warning("ygoprodeck_sync.skipped_already_running")
            return None
        try:
            # shrink_floor inside the try so a misconfigured tolerance records FAILED too.
            floor = shrink_floor(
                SyncKind.YGOPRODECK_METADATA,
                "card_count",
                tolerance=settings.SYNC_GUARD_METADATA_TOLERANCE,
            )
            provider = YgoprodeckProvider(fetch, min_cards=floor)
            # The Phase 5 archetype-withdrawal guard: fail closed if this one run would
            # null archetype on more than SYNC_GUARD_ARCHETYPE_TOLERANCE of the currently
            # tagged cards (a partial/total upstream key-drop), without blocking legit
            # small corrections (the absolute floor inside the guard).
            result = sync_cards_from_metadata(
                provider, archetype_withdrawal_tolerance=settings.SYNC_GUARD_ARCHETYPE_TOLERANCE
            )
        except Exception as exc:
            record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.FAILED, error=str(exc))
            raise
        record_run(
            SyncKind.YGOPRODECK_METADATA,
            SyncStatus.SUCCESS,
            card_count=result.cards_created + result.cards_updated + result.cards_unchanged,
            printing_count=(
                result.printings_created + result.printings_updated + result.printings_unchanged
            ),
            detail=asdict(result),
        )
        return result
