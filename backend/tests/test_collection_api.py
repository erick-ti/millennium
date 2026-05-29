from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.cards.models import Card, CardPrinting
from apps.collection.models import (
    CollectionItem,
    CollectionLot,
    Condition,
    Language,
    StorageLocation,
)
from apps.core.enums import Edition
from apps.portfolio.models import Portfolio


@pytest.fixture
def client() -> APIClient:
    user = get_user_model().objects.create_user("reader", "r@example.com", "x")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _printing(set_code: str = "L5DD-ENC09", set_rarity: str = "Common") -> CardPrinting:
    card = Card.objects.create(name="Ash Blossom & Joyous Spring")
    return CardPrinting.objects.create(
        card=card, set_code=set_code, set_rarity=set_rarity, set_name="set"
    )


def _item(
    *,
    portfolio: Portfolio | None = None,
    printing: CardPrinting | None = None,
    condition: Condition = Condition.NEAR_MINT,
    edition: Edition = Edition.FIRST_EDITION,
    language: Language = Language.ENGLISH,
    location: StorageLocation | None = None,
) -> CollectionItem:
    # get_or_create on the default portfolio so multiple _item() calls without an
    # explicit portfolio share it instead of colliding on Portfolio.name's UNIQUE.
    portfolio = portfolio or Portfolio.objects.get_or_create(name="Yubel Deck")[0]
    printing = printing or _printing()
    return CollectionItem.objects.create(
        portfolio=portfolio,
        printing=printing,
        condition=condition,
        edition=edition,
        language=language,
        storage_location=location,
    )


def _lot(
    item: CollectionItem,
    *,
    quantity: int = 1,
    unit_cost: Decimal | None = Decimal("0.68"),
    acquired_at: date | None = date(2025, 1, 15),
    import_source_ref: str | None = None,
) -> CollectionLot:
    return CollectionLot.objects.create(
        collection_item=item,
        quantity=quantity,
        unit_cost=unit_cost,
        acquired_at=acquired_at,
        import_source_ref=import_source_ref,
    )


# --- auth -----------------------------------------------------------------------


@pytest.mark.django_db
def test_endpoints_require_authentication() -> None:
    anon = APIClient()
    assert anon.get(reverse("collection:collectionitem-list")).status_code == 403
    assert anon.get(reverse("collection:collectionlot-list")).status_code == 403


# --- items ---------------------------------------------------------------------


@pytest.mark.django_db
def test_item_list_aggregates_quantity_from_lots(client: APIClient) -> None:
    """``quantity`` isn't stored on the item (DECISIONS 2026-05-18) — the list
    response derives it from the SUM over child lots."""
    item = _item()
    _lot(item, quantity=2)
    _lot(item, quantity=1)

    resp = client.get(reverse("collection:collectionitem-list"))

    assert resp.status_code == status.HTTP_200_OK
    [row] = resp.data["results"]
    assert row["quantity"] == 3
    # Denormalized identity fields for the slice-3 table view.
    assert row["portfolio_name"] == "Yubel Deck"
    assert row["card_name"] == "Ash Blossom & Joyous Spring"
    assert row["set_code"] == "L5DD-ENC09"


@pytest.mark.django_db
def test_item_with_no_lots_reads_quantity_zero(client: APIClient) -> None:
    """An item without lots reads as quantity 0, not NULL — Coalesce(SUM, 0)."""
    _item()

    resp = client.get(reverse("collection:collectionitem-list"))

    assert resp.status_code == status.HTTP_200_OK
    [row] = resp.data["results"]
    assert row["quantity"] == 0


@pytest.mark.django_db
def test_item_list_filters_by_portfolio(client: APIClient) -> None:
    yubel = Portfolio.objects.create(name="Yubel Deck")
    longterm = Portfolio.objects.create(name="Long-term hold")
    yubel_item = _item(portfolio=yubel)
    _item(portfolio=longterm, printing=_printing(set_code="MAMA-EN036", set_rarity="Ultra Rare"))

    resp = client.get(reverse("collection:collectionitem-list"), {"portfolio": yubel.pk})

    assert resp.status_code == status.HTTP_200_OK
    [row] = resp.data["results"]
    assert row["id"] == yubel_item.id


@pytest.mark.django_db
def test_item_list_rejects_invalid_portfolio_filter(client: APIClient) -> None:
    resp = client.get(
        reverse("collection:collectionitem-list"), {"portfolio": "not-an-int"}
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_item_detail_nests_lots(client: APIClient) -> None:
    item = _item()
    lot1 = _lot(item, quantity=2, unit_cost=Decimal("0.68"), acquired_at=date(2025, 1, 15))
    lot2 = _lot(
        item, quantity=1, unit_cost=None, acquired_at=None,
        import_source_ref="dragon_shield:item:42",
    )

    resp = client.get(reverse("collection:collectionitem-detail", args=[item.pk]))

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["quantity"] == 3
    lot_ids = [lot["id"] for lot in resp.data["lots"]]
    # Meta.ordering = (item, acquired_at-asc-nulls-last, id) — known-date lot first.
    assert lot_ids == [lot1.id, lot2.id]
    # NULL unit_cost / acquired_at are NULL, not 0/today (the fake-zero avoidance posture).
    [_, unknown_lot] = resp.data["lots"]
    assert unknown_lot["unit_cost"] is None
    assert unknown_lot["acquired_at"] is None
    assert unknown_lot["import_source_ref"] == "dragon_shield:item:42"


@pytest.mark.django_db
def test_item_carries_storage_location_when_set(client: APIClient) -> None:
    location = StorageLocation.objects.create(name="Binder A page 3")
    item = _item(location=location)
    _lot(item)

    resp = client.get(reverse("collection:collectionitem-list"))

    [row] = resp.data["results"]
    assert row["storage_location_name"] == "Binder A page 3"


@pytest.mark.django_db
def test_item_storage_location_null_serializes_as_null(client: APIClient) -> None:
    item = _item()
    _lot(item)

    resp = client.get(reverse("collection:collectionitem-list"))

    [row] = resp.data["results"]
    assert row["storage_location"] is None
    assert row["storage_location_name"] is None


# --- lots ----------------------------------------------------------------------


@pytest.mark.django_db
def test_lot_list_filters_by_item(client: APIClient) -> None:
    item_a = _item()
    item_b = _item(printing=_printing(set_code="MAMA-EN036", set_rarity="Ultra Rare"))
    lot_a = _lot(item_a)
    _lot(item_b)

    resp = client.get(reverse("collection:collectionlot-list"), {"item": item_a.pk})

    assert resp.status_code == status.HTTP_200_OK
    [row] = resp.data["results"]
    assert row["id"] == lot_a.id


@pytest.mark.django_db
def test_lot_list_filters_by_portfolio_via_item(client: APIClient) -> None:
    yubel = Portfolio.objects.create(name="Yubel Deck")
    longterm = Portfolio.objects.create(name="Long-term hold")
    yubel_item = _item(portfolio=yubel)
    longterm_item = _item(
        portfolio=longterm, printing=_printing(set_code="MAMA-EN036", set_rarity="Ultra Rare")
    )
    yubel_lot = _lot(yubel_item)
    _lot(longterm_item)

    resp = client.get(reverse("collection:collectionlot-list"), {"portfolio": yubel.pk})

    [row] = resp.data["results"]
    assert row["id"] == yubel_lot.id


@pytest.mark.django_db
def test_lot_list_rejects_invalid_filter(client: APIClient) -> None:
    resp = client.get(reverse("collection:collectionlot-list"), {"item": "x"})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_lot_detail_returns_full_shape(client: APIClient) -> None:
    item = _item()
    lot = _lot(item)

    resp = client.get(reverse("collection:collectionlot-detail", args=[lot.pk]))

    assert resp.status_code == status.HTTP_200_OK
    assert set(resp.data) == {
        "id",
        "collection_item",
        "quantity",
        "unit_cost",
        "acquired_at",
        "import_source_ref",
        "created_at",
        "updated_at",
    }


@pytest.mark.django_db
def test_lot_unknown_cost_serializes_as_null(client: APIClient) -> None:
    """NULL ``unit_cost`` is "unknown" — the slice-4a coverage representation reads it
    distinctly from 0 to avoid fake-zero cost basis in valuation roll-ups."""
    item = _item()
    _lot(item, unit_cost=None)

    resp = client.get(reverse("collection:collectionlot-list"))

    [lot] = resp.data["results"]
    assert lot["unit_cost"] is None


def _flatten_results(resp: Any) -> set[int]:
    return {row["id"] for row in resp.data["results"]}
