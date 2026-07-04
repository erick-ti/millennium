from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from django.utils import timezone

from apps.core.enums import Edition
from apps.pricing.models import ExternalPriceId, PriceSnapshot, Provider
from apps.pricing.providers.base import PriceData

# TCGCSV subTypeName → Edition. Sealed "Normal" rows are already dropped by the
# adapter; any other unrecognized subtype is skipped here, never coerced into an
# edition (join on productId first, then map editions).
_SUBTYPE_TO_EDITION = {
    "1st Edition": Edition.FIRST_EDITION,
    "Unlimited": Edition.UNLIMITED,
    "Limited": Edition.LIMITED,
}


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Per-run counts from ingesting provider prices into ``price_snapshots``."""

    prices_seen: int = 0
    snapshots_created: int = 0
    snapshots_existing: int = 0
    skipped_unmatched_product: int = 0
    skipped_conflicted_product: int = 0
    skipped_unknown_subtype: int = 0
    skipped_no_price: int = 0


def ingest_prices(
    prices: Iterable[PriceData],
    *,
    snapshot_date: date | None = None,
    excluded_external_ids: Iterable[str] = (),
) -> IngestResult:
    """Write append-only ``PriceSnapshot`` rows from a provider's price rows for one day.

    The ``external_price_ids`` join is the gate: a price row is
    snapshotted only when its ``external_id`` (productId) resolves to a ``CardPrinting``
    that reconciliation already matched, since unmatched / non-single-card ids have no mapping
    and are skipped. ``excluded_external_ids`` are the ids reconciliation flagged as an
    ``EXTERNAL_ID_CONFLICT`` *this run* (their ``ExternalPriceId`` points at a different
    printing than the current catalog); they still have a stale mapping, so pricing must
    not trust it. The caller passes the live run's set rather than ingestion querying the
    review queue, so a re-conflict on an id a human previously marked resolved/ignored is
    still skipped (the queue's triage status is mutable and would miss it). ``subtype_name``
    then maps to an ``Edition``; an unrecognized subtype is skipped, not coerced.
    Append-only and idempotent: the natural key ``(printing, edition, source, day)`` is
    ``get_or_create``'d, so a same-day re-run is a no-op (the first capture stands,
    prices are never overwritten). Single-writer, no row locking.
    """
    # timezone.localdate() (not date.today()) so the date is the project's UTC day
    # (TIME_ZONE/USE_TZ), not the worker's OS-local day. snapshot_date is part of the
    # append-only natural key, so an off-by-one near midnight would misbucket the series.
    day = snapshot_date or timezone.localdate()
    prices_seen = snapshots_created = snapshots_existing = 0
    skipped_unmatched_product = skipped_unknown_subtype = skipped_no_price = 0
    skipped_conflicted_product = 0

    # One query for the productId → printing map rather than a lookup per price row.
    printing_by_external_id: dict[str, int] = {
        external_id: printing_id
        for external_id, printing_id in ExternalPriceId.objects.filter(
            provider=Provider.TCGCSV
        ).values_list("external_id", "printing_id")
    }
    # Ids reconciliation flagged as contested this run. Don't price through their stale
    # mapping (see the docstring); sourced from the live run, not the queue's triage status.
    conflicted_ids = set(excluded_external_ids)

    for price in prices:
        prices_seen += 1
        external_id = price.external_id.strip()
        if external_id in conflicted_ids:
            skipped_conflicted_product += 1
            continue
        printing_id = printing_by_external_id.get(external_id)
        if printing_id is None:
            skipped_unmatched_product += 1
            continue
        edition = _SUBTYPE_TO_EDITION.get(price.subtype_name or "")
        if edition is None:
            skipped_unknown_subtype += 1
            continue
        if (
            price.low_price is None
            and price.mid_price is None
            and price.high_price is None
            and price.market_price is None
            and price.direct_low_price is None
        ):
            # No usable price. Don't write a priceless snapshot: it's noise, and the
            # same-day get_or_create would lock it in against a later good row. A missing
            # price is a coverage gap (no snapshot), not a snapshot-that-isn't-a-price.
            skipped_no_price += 1
            continue
        _, created = PriceSnapshot.objects.get_or_create(
            printing_id=printing_id,
            edition=edition,
            source=Provider.TCGCSV,
            snapshot_date=day,
            defaults={
                "low_price": price.low_price,
                "mid_price": price.mid_price,
                "high_price": price.high_price,
                "market_price": price.market_price,
                "direct_low_price": price.direct_low_price,
                "source_subtype_name": price.subtype_name,
            },
        )
        if created:
            snapshots_created += 1
        else:
            snapshots_existing += 1

    return IngestResult(
        prices_seen=prices_seen,
        snapshots_created=snapshots_created,
        snapshots_existing=snapshots_existing,
        skipped_unmatched_product=skipped_unmatched_product,
        skipped_conflicted_product=skipped_conflicted_product,
        skipped_unknown_subtype=skipped_unknown_subtype,
        skipped_no_price=skipped_no_price,
    )
