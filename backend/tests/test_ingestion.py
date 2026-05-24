from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.cards.models import Card, CardPrinting
from apps.core.enums import Edition
from apps.pricing.ingestion import ingest_prices
from apps.pricing.models import (
    ExternalPriceId,
    PriceSnapshot,
    Provider,
    UnmatchedProduct,
    UnmatchedReason,
    UnmatchedStatus,
)
from apps.pricing.providers.base import PriceData, ProductListing
from apps.pricing.reconciliation import reconcile_products_to_printings

_DAY = date(2026, 5, 24)


def _priced_printing(set_code: str, external_id: str) -> CardPrinting:
    """A printing already matched to a TCGCSV productId — reconciliation's output."""
    card = Card.objects.create(name=f"Card {set_code}")
    printing = CardPrinting.objects.create(
        card=card, set_code=set_code, set_rarity="Ultra Rare", set_name="Some Set"
    )
    ExternalPriceId.objects.create(
        provider=Provider.TCGCSV, external_id=external_id, printing=printing
    )
    return printing


def _price(
    external_id: str,
    subtype: str | None,
    *,
    market: Decimal | None = None,
    low: Decimal | None = None,
) -> PriceData:
    return PriceData(external_id=external_id, subtype_name=subtype, low_price=low, market_price=market)


@pytest.mark.django_db
def test_ingests_snapshot_for_matched_printing() -> None:
    printing = _priced_printing("MP25-EN172", "651572")

    result = ingest_prices(
        [_price("651572", "1st Edition", market=Decimal("0.24"), low=Decimal("0.10"))],
        snapshot_date=_DAY,
    )

    assert result.snapshots_created == 1
    snap = PriceSnapshot.objects.get()
    assert snap.printing == printing
    assert snap.edition == Edition.FIRST_EDITION
    assert snap.source == Provider.TCGCSV
    assert snap.snapshot_date == _DAY
    assert snap.market_price == Decimal("0.24")
    assert snap.low_price == Decimal("0.10")
    assert snap.source_subtype_name == "1st Edition"
    assert snap.confidence == 1.0


@pytest.mark.django_db
def test_skips_unmatched_product() -> None:
    """A price row whose productId was never matched (no external_price_id) is skipped —
    the join through external_price_ids is the single-card gate."""
    result = ingest_prices([_price("999999", "1st Edition", market=Decimal("1.00"))], snapshot_date=_DAY)

    assert result.skipped_unmatched_product == 1
    assert result.snapshots_created == 0
    assert PriceSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_skips_unknown_subtype() -> None:
    """An unrecognized subtype is skipped, not coerced into an edition."""
    _priced_printing("MP25-EN172", "651572")

    result = ingest_prices([_price("651572", "Sealed Deck", market=Decimal("5.00"))], snapshot_date=_DAY)

    assert result.skipped_unknown_subtype == 1
    assert PriceSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_maps_each_single_card_edition() -> None:
    p1 = _priced_printing("A-1", "1")
    p2 = _priced_printing("A-2", "2")
    p3 = _priced_printing("A-3", "3")

    ingest_prices(
        [
            _price("1", "1st Edition", market=Decimal("1.00")),
            _price("2", "Unlimited", market=Decimal("2.00")),
            _price("3", "Limited", market=Decimal("3.00")),
        ],
        snapshot_date=_DAY,
    )

    assert PriceSnapshot.objects.get(printing=p1).edition == Edition.FIRST_EDITION
    assert PriceSnapshot.objects.get(printing=p2).edition == Edition.UNLIMITED
    assert PriceSnapshot.objects.get(printing=p3).edition == Edition.LIMITED


@pytest.mark.django_db
def test_same_day_reingest_is_noop() -> None:
    """Append-only: a same-day re-run finds the existing snapshot and writes nothing."""
    _priced_printing("MP25-EN172", "651572")
    prices = [_price("651572", "1st Edition", market=Decimal("0.24"))]
    ingest_prices(prices, snapshot_date=_DAY)

    second = ingest_prices(prices, snapshot_date=_DAY)

    assert second.snapshots_created == 0
    assert second.snapshots_existing == 1
    assert PriceSnapshot.objects.count() == 1


@pytest.mark.django_db
def test_distinct_editions_of_one_printing_are_separate_snapshots() -> None:
    """Edition is a pricing dimension: 1st Edition and Unlimited rows for one printing
    on one day are two snapshots (Aqua Madoor's two TCGCSV price rows)."""
    _priced_printing("LOB-040", "21747")

    ingest_prices(
        [
            _price("21747", "1st Edition", market=Decimal("0.44")),
            _price("21747", "Unlimited", market=Decimal("0.25")),
        ],
        snapshot_date=_DAY,
    )

    assert PriceSnapshot.objects.count() == 2


@pytest.mark.django_db
def test_skips_row_with_no_usable_price() -> None:
    """A matched row whose every price point is null writes no snapshot — a coverage
    gap, not a priceless snapshot the same-day get_or_create would lock in."""
    _priced_printing("MP25-EN172", "651572")

    result = ingest_prices([_price("651572", "1st Edition")], snapshot_date=_DAY)  # no prices

    assert result.skipped_no_price == 1
    assert result.snapshots_created == 0
    assert PriceSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_default_snapshot_date_uses_timezone_localdate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default date is Django's project (UTC) date via timezone.localdate(), not the
    OS-local date.today(), so a non-UTC worker can't misdate the daily series."""
    # Patch localdate on the shared django.utils.timezone module that ingestion calls.
    sentinel = date(2099, 1, 2)
    monkeypatch.setattr(timezone, "localdate", lambda: sentinel)
    _priced_printing("MP25-EN172", "651572")

    ingest_prices([_price("651572", "1st Edition", market=Decimal("0.24"))])  # no snapshot_date

    assert PriceSnapshot.objects.get().snapshot_date == sentinel


class _FakeProvider:
    """Stands in for TcgcsvProvider in the command test — no network."""

    def fetch_products(self) -> list[ProductListing]:
        return [
            ProductListing(
                external_id="651572",
                set_code="MP25-EN172",
                set_rarity="Ultra Rare",
                name="Eternal Favorite",
                set_name="Maximum Pride 2025",
            )
        ]

    def fetch_prices(self) -> list[PriceData]:
        return [PriceData(external_id="651572", subtype_name="1st Edition", market_price=Decimal("0.24"))]


@pytest.mark.django_db
def test_sync_tcgcsv_command_runs_full_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The command wires provider → reconcile → ingest: it matches a product to an
    existing printing, writes the external id, then snapshots its price."""
    card = Card.objects.create(name="Eternal Favorite")
    CardPrinting.objects.create(
        card=card, set_code="MP25-EN172", set_rarity="Ultra Rare", set_name="Maximum Pride 2025"
    )
    # The command runs run_tcgcsv_sync, which constructs TcgcsvProvider with injected
    # floors; swap it at that construction site for the fake (ignoring those args).
    monkeypatch.setattr("apps.pricing.sync.TcgcsvProvider", lambda *a, **k: _FakeProvider())
    call_command("sync_tcgcsv")

    assert ExternalPriceId.objects.filter(provider=Provider.TCGCSV, external_id="651572").exists()
    assert PriceSnapshot.objects.filter(source=Provider.TCGCSV).count() == 1


@pytest.mark.django_db
def test_conflicted_external_id_is_not_priced_through_stale_mapping() -> None:
    """Whole-pipeline regression (full-session review): a productId whose stale mapping
    points at the wrong printing is flagged EXTERNAL_ID_CONFLICT by reconciliation, and
    ingestion then refuses to price through that mapping — else it would snapshot onto
    the wrong printing, append-only and hard to repair."""
    card = Card.objects.create(name="Eternal Favorite")
    stale = CardPrinting.objects.create(
        card=card, set_code="OLD-001", set_rarity="Common", set_name="Old Set"
    )
    ExternalPriceId.objects.create(provider=Provider.TCGCSV, external_id="651572", printing=stale)
    # The current catalog resolves 651572 to a different printing.
    CardPrinting.objects.create(
        card=card, set_code="MP25-EN172", set_rarity="Ultra Rare", set_name="MP25"
    )

    rec = reconcile_products_to_printings(
        [
            ProductListing(
                external_id="651572",
                set_code="MP25-EN172",
                set_rarity="Ultra Rare",
                name="Eternal Favorite",
                set_name="MP25",
            )
        ]
    )
    assert UnmatchedProduct.objects.filter(reason=UnmatchedReason.EXTERNAL_ID_CONFLICT).count() == 1
    assert rec.conflicted_external_ids == {"651572"}

    result = ingest_prices(
        [_price("651572", "1st Edition", market=Decimal("0.24"))],
        snapshot_date=_DAY,
        excluded_external_ids=rec.conflicted_external_ids,
    )

    assert result.skipped_conflicted_product == 1
    assert PriceSnapshot.objects.count() == 0  # not priced through the stale mapping


@pytest.mark.django_db
def test_reconflicted_resolved_id_is_still_skipped() -> None:
    """A conflict a human previously marked RESOLVED (without fixing the mapping) that
    re-conflicts this run is still skipped: ingestion uses reconciliation's live run-set,
    not the queue's mutable status (which update_or_create preserves across reruns)."""
    card = Card.objects.create(name="Eternal Favorite")
    stale = CardPrinting.objects.create(
        card=card, set_code="OLD-001", set_rarity="Common", set_name="Old Set"
    )
    ExternalPriceId.objects.create(provider=Provider.TCGCSV, external_id="651572", printing=stale)
    CardPrinting.objects.create(
        card=card, set_code="MP25-EN172", set_rarity="Ultra Rare", set_name="MP25"
    )
    UnmatchedProduct.objects.create(
        provider=Provider.TCGCSV,
        external_id="651572",
        set_code="MP25-EN172",
        set_rarity="Ultra Rare",
        product_name="Eternal Favorite",
        reason=UnmatchedReason.EXTERNAL_ID_CONFLICT,
        status=UnmatchedStatus.RESOLVED,  # a human marked it resolved earlier
    )

    rec = reconcile_products_to_printings(
        [
            ProductListing(
                external_id="651572",
                set_code="MP25-EN172",
                set_rarity="Ultra Rare",
                name="Eternal Favorite",
                set_name="MP25",
            )
        ]
    )
    # The re-queue preserves the stale RESOLVED status, but the live run still flags it.
    assert UnmatchedProduct.objects.get(external_id="651572").status == UnmatchedStatus.RESOLVED
    assert "651572" in rec.conflicted_external_ids

    result = ingest_prices(
        [_price("651572", "1st Edition", market=Decimal("0.24"))],
        snapshot_date=_DAY,
        excluded_external_ids=rec.conflicted_external_ids,
    )

    assert result.skipped_conflicted_product == 1
    assert PriceSnapshot.objects.count() == 0
