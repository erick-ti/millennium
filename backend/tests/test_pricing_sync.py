from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

import pytest

from apps.cards.models import Card, CardPrinting
from apps.core.models import SyncKind, SyncRun, SyncStatus
from apps.core.sync_history import record_run
from apps.pricing.models import ExternalPriceId, PriceSnapshot, Provider
from apps.pricing.providers.base import PriceData, ProductListing
from apps.pricing.sync import run_tcgcsv_sync


class _FakeProvider:
    """Stands in for TcgcsvProvider (the network boundary) so reconcile + ingest run
    for real. The provider's own floor *enforcement* is covered in test_providers.py;
    here we drive the orchestration that computes and injects those floors."""

    def __init__(
        self,
        *,
        products: list[ProductListing] | None = None,
        prices: list[PriceData] | None = None,
        raise_on_products: Exception | None = None,
    ) -> None:
        self._products = products or []
        self._prices = prices or []
        self._raise = raise_on_products

    def fetch_products(self) -> list[ProductListing]:
        if self._raise is not None:
            raise self._raise
        return self._products

    def fetch_prices(self) -> list[PriceData]:
        return self._prices


def _install(monkeypatch: pytest.MonkeyPatch, provider: _FakeProvider) -> dict[str, object]:
    """Swap TcgcsvProvider at the orchestration's construction site, capturing the
    floor kwargs the orchestration injected so a test can assert them."""
    captured: dict[str, object] = {}

    def factory(_fetch: object, **kwargs: object) -> _FakeProvider:
        captured.update(kwargs)
        return provider

    monkeypatch.setattr("apps.pricing.sync.TcgcsvProvider", factory)
    return captured


@pytest.mark.django_db
def test_run_tcgcsv_sync_records_success_and_runs_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    card = Card.objects.create(name="Eternal Favorite")
    CardPrinting.objects.create(
        card=card, set_code="MP25-EN172", set_rarity="Ultra Rare", set_name="Maximum Pride 2025"
    )
    provider = _FakeProvider(
        products=[
            ProductListing(
                external_id="651572",
                set_code="MP25-EN172",
                set_rarity="Ultra Rare",
                name="Eternal Favorite",
                set_name="Maximum Pride 2025",
            )
        ],
        prices=[PriceData(external_id="651572", subtype_name="1st Edition", market_price=Decimal("0.24"))],
    )
    _install(monkeypatch, provider)

    outcome = run_tcgcsv_sync(fetch=lambda _url: None)

    assert outcome is not None
    rec, ing = outcome
    assert rec.exact_matched == 1
    assert ing.snapshots_created == 1
    assert ExternalPriceId.objects.filter(provider=Provider.TCGCSV, external_id="651572").exists()
    assert PriceSnapshot.objects.filter(source=Provider.TCGCSV).count() == 1

    run = SyncRun.objects.get(kind=SyncKind.TCGCSV_PRICING, status=SyncStatus.SUCCESS)
    assert (run.product_count, run.price_row_count) == (1, 1)
    assert run.card_count is None
    assert set(run.detail) == {"reconcile", "ingest"}


@pytest.mark.django_db
def test_run_tcgcsv_sync_injects_dynamic_floors_from_history(monkeypatch: pytest.MonkeyPatch) -> None:
    record_run(SyncKind.TCGCSV_PRICING, SyncStatus.SUCCESS, product_count=1000, price_row_count=500)
    captured = _install(monkeypatch, _FakeProvider())

    run_tcgcsv_sync(fetch=lambda _url: None)

    # 1000 * (1 - 0.10) = 900; 500 * (1 - 0.10) = 450
    assert captured == {"min_products": 900, "min_price_rows": 450}


@pytest.mark.django_db
def test_run_tcgcsv_sync_first_run_injects_no_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """No history → None floors, so the provider falls back to its bootstrap floors."""
    captured = _install(monkeypatch, _FakeProvider())

    run_tcgcsv_sync(fetch=lambda _url: None)

    assert captured == {"min_products": None, "min_price_rows": None}


@pytest.mark.django_db
def test_run_tcgcsv_sync_failure_records_failed_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        _FakeProvider(raise_on_products=ValueError("below the sanity floor — truncated")),
    )

    with pytest.raises(ValueError, match="floor"):
        run_tcgcsv_sync(fetch=lambda _url: None)

    assert not ExternalPriceId.objects.exists()  # rejected at fetch, before any write
    failed = SyncRun.objects.get(kind=SyncKind.TCGCSV_PRICING, status=SyncStatus.FAILED)
    assert "floor" in failed.error


@pytest.mark.django_db
def test_run_tcgcsv_sync_skips_when_lock_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """If another run holds the advisory lock, this one skips: returns None, touches
    nothing, and records no SyncRun (it never ran) -- so concurrent invocations can't
    race the single-writer reconcile paths (adversarial-review F2)."""

    @contextmanager
    def _held(_kind: object) -> Iterator[bool]:
        yield False

    monkeypatch.setattr("apps.pricing.sync.sync_lock", _held)

    result = run_tcgcsv_sync(fetch=lambda _url: None)

    assert result is None
    assert not SyncRun.objects.exists()
    assert not ExternalPriceId.objects.exists()
