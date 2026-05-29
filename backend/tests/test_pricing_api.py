from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.cards.models import Card, CardPrinting
from apps.core.enums import Edition
from apps.pricing.models import PriceSnapshot, Provider


@pytest.fixture
def client() -> APIClient:
    user = get_user_model().objects.create_user("reader", "r@example.com", "x")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _printing(set_code: str = "L5DD-ENC09") -> CardPrinting:
    card = Card.objects.create(name="Ash Blossom & Joyous Spring")
    return CardPrinting.objects.create(
        card=card, set_code=set_code, set_rarity="Common", set_name="set"
    )


def _snapshot(
    printing: CardPrinting,
    *,
    snapshot_date: date = date(2026, 1, 15),
    edition: Edition = Edition.FIRST_EDITION,
    market_price: Decimal | None = Decimal("3.50"),
    mid_price: Decimal | None = Decimal("3.25"),
    low_price: Decimal | None = Decimal("3.00"),
) -> PriceSnapshot:
    return PriceSnapshot.objects.create(
        printing=printing,
        edition=edition,
        source=Provider.TCGCSV,
        snapshot_date=snapshot_date,
        market_price=market_price,
        mid_price=mid_price,
        low_price=low_price,
    )


# --- auth -----------------------------------------------------------------------


@pytest.mark.django_db
def test_endpoints_require_authentication() -> None:
    anon = APIClient()
    assert anon.get(reverse("pricing:pricesnapshot-list")).status_code == 403
    assert anon.get(reverse("pricing:pricesnapshot-latest")).status_code == 403


# --- list / filter -------------------------------------------------------------


@pytest.mark.django_db
def test_snapshot_list_filters_by_printing(client: APIClient) -> None:
    p1 = _printing("L5DD-ENC09")
    p2 = _printing("MAMA-EN036")
    s1 = _snapshot(p1)
    _snapshot(p2)

    resp = client.get(reverse("pricing:pricesnapshot-list"), {"printing": p1.pk})

    [row] = resp.data["results"]
    assert row["id"] == s1.id


@pytest.mark.django_db
def test_snapshot_list_filters_by_edition(client: APIClient) -> None:
    p = _printing()
    first = _snapshot(p, edition=Edition.FIRST_EDITION)
    _snapshot(p, edition=Edition.UNLIMITED)

    resp = client.get(reverse("pricing:pricesnapshot-list"), {"edition": Edition.FIRST_EDITION.value})

    [row] = resp.data["results"]
    assert row["id"] == first.id


@pytest.mark.django_db
def test_snapshot_list_filters_by_date_range(client: APIClient) -> None:
    p = _printing()
    jan_10 = _snapshot(p, snapshot_date=date(2026, 1, 10))
    jan_15 = _snapshot(p, snapshot_date=date(2026, 1, 15))
    _snapshot(p, snapshot_date=date(2026, 1, 20))

    resp = client.get(
        reverse("pricing:pricesnapshot-list"),
        {"from": "2026-01-10", "to": "2026-01-15"},
    )

    ids = {row["id"] for row in resp.data["results"]}
    assert ids == {jan_10.id, jan_15.id}


@pytest.mark.django_db
def test_snapshot_list_rejects_invalid_edition(client: APIClient) -> None:
    resp = client.get(reverse("pricing:pricesnapshot-list"), {"edition": "bogus"})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_snapshot_list_rejects_malformed_date(client: APIClient) -> None:
    resp = client.get(reverse("pricing:pricesnapshot-list"), {"to": "not-a-date"})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_snapshot_nullable_price_points_serialize_as_null(client: APIClient) -> None:
    """A provider may report only some price points — NULL ones stay NULL on the
    response (the slice-4a fake-zero-avoidance posture, applied to pricing)."""
    p = _printing()
    _snapshot(p, market_price=None, mid_price=Decimal("3.25"), low_price=None)

    resp = client.get(reverse("pricing:pricesnapshot-list"))

    [row] = resp.data["results"]
    assert row["market_price"] is None
    assert row["low_price"] is None
    assert row["mid_price"] == "3.25"


# --- latest action -------------------------------------------------------------


@pytest.mark.django_db
def test_latest_returns_most_recent_for_printing_edition(client: APIClient) -> None:
    p = _printing()
    _snapshot(p, snapshot_date=date(2026, 1, 10))
    latest = _snapshot(p, snapshot_date=date(2026, 1, 15))
    _snapshot(p, edition=Edition.UNLIMITED, snapshot_date=date(2026, 1, 15))  # different edition

    resp = client.get(
        reverse("pricing:pricesnapshot-latest"),
        {"printing": str(p.pk), "edition": Edition.FIRST_EDITION.value},
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["id"] == latest.id


@pytest.mark.django_db
def test_latest_returns_404_when_no_snapshot(client: APIClient) -> None:
    p = _printing()
    # No snapshots for this (printing, edition).

    resp = client.get(
        reverse("pricing:pricesnapshot-latest"),
        {"printing": str(p.pk), "edition": Edition.FIRST_EDITION.value},
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_latest_requires_printing_and_edition(client: APIClient) -> None:
    p = _printing()

    # Missing edition → 400.
    resp_missing_edition = client.get(
        reverse("pricing:pricesnapshot-latest"), {"printing": p.pk}
    )
    assert resp_missing_edition.status_code == status.HTTP_400_BAD_REQUEST

    # Missing printing → 400.
    resp_missing_printing = client.get(
        reverse("pricing:pricesnapshot-latest"), {"edition": Edition.FIRST_EDITION.value}
    )
    assert resp_missing_printing.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_latest_rejects_invalid_edition(client: APIClient) -> None:
    p = _printing()
    resp = client.get(
        reverse("pricing:pricesnapshot-latest"), {"printing": str(p.pk), "edition": "bogus"}
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
