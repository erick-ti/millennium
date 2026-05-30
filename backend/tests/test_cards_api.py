from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.cards.models import Card, CardPrinting

# --- fixtures -------------------------------------------------------------------


@pytest.fixture
def client() -> APIClient:
    """An authenticated APIClient — every viewset inherits the DRF default
    ``IsAuthenticated``, so anonymous calls 403 (the imports-API + Invariant 7 posture)."""
    user = get_user_model().objects.create_user("reader", "r@example.com", "x")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _card(name: str = "Ash Blossom & Joyous Spring", passcode: int | None = 14558127) -> Card:
    return Card.objects.create(name=name, passcode=passcode)


def _printing(
    card: Card,
    *,
    set_code: str = "L5DD-ENC09",
    set_rarity: str = "Common",
    set_name: str = "Legendary Decks 5: Dragons & Duelists",
    variant_label: str | None = None,
    is_multi_variant: bool = False,
) -> CardPrinting:
    return CardPrinting.objects.create(
        card=card,
        set_code=set_code,
        set_rarity=set_rarity,
        set_name=set_name,
        variant_label=variant_label,
        is_multi_variant=is_multi_variant,
    )


# --- auth -----------------------------------------------------------------------


@pytest.mark.django_db
def test_endpoints_require_authentication() -> None:
    anon = APIClient()
    assert anon.get(reverse("cards:card-list")).status_code == status.HTTP_403_FORBIDDEN
    assert anon.get(reverse("cards:cardprinting-list")).status_code == status.HTTP_403_FORBIDDEN


# --- cards ---------------------------------------------------------------------


@pytest.mark.django_db
def test_card_list_returns_paginated_shape(client: APIClient) -> None:
    ash = _card()
    nibiru = _card(name="Nibiru, the Primal Being", passcode=27204311)

    resp = client.get(reverse("cards:card-list"))

    assert resp.status_code == status.HTTP_200_OK
    body = resp.data
    assert body["count"] == 2
    assert {"count", "next", "previous", "results"} <= set(body)
    # Ordered by name; "Ash" < "Nibiru".
    [first, second] = body["results"]
    assert first == {
        "id": ash.id,
        "passcode": 14558127,
        "name": "Ash Blossom & Joyous Spring",
        "printings_count": 0,
    }
    assert second == {
        "id": nibiru.id,
        "passcode": 27204311,
        "name": "Nibiru, the Primal Being",
        "printings_count": 0,
    }


@pytest.mark.django_db
def test_card_list_includes_printings_count(client: APIClient) -> None:
    """The slice-4 /cards table renders a per-card printing count (a
    ``Count("printings")`` annotation on the viewset, not a stored field). A
    card with no printings reads 0."""
    ash = _card()  # gets two printings
    _printing(ash, set_code="L5DD-ENC09", set_rarity="Common")
    _printing(ash, set_code="MAMA-EN036", set_rarity="Ultra Rare")
    nibiru = _card(name="Nibiru, the Primal Being", passcode=27204311)  # no printings

    resp = client.get(reverse("cards:card-list"))

    assert resp.status_code == status.HTTP_200_OK
    counts = {row["id"]: row["printings_count"] for row in resp.data["results"]}
    assert counts == {ash.id: 2, nibiru.id: 0}


@pytest.mark.django_db
def test_card_detail_nests_printings(client: APIClient) -> None:
    card = _card()
    p1 = _printing(card, set_code="L5DD-ENC09", set_rarity="Common")
    p2 = _printing(
        card, set_code="MAMA-EN036", set_rarity="Ultra Rare", set_name="Maximum Gold: El Dorado"
    )

    resp = client.get(reverse("cards:card-detail", args=[card.pk]))

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["id"] == card.id
    assert resp.data["name"] == "Ash Blossom & Joyous Spring"
    # The annotation is present on retrieve too (CardDetailSerializer inherits
    # the field), so detail can't AttributeError on a missing attribute.
    assert resp.data["printings_count"] == 2
    nested_ids = {p["id"] for p in resp.data["printings"]}
    assert nested_ids == {p1.id, p2.id}
    # Detail printings carry the same fields as the flat printings endpoint.
    fields = set(resp.data["printings"][0])
    assert fields == {
        "id",
        "card",
        "card_name",
        "set_code",
        "set_rarity",
        "variant_label",
        "set_name",
        "is_multi_variant",
    }


@pytest.mark.django_db
def test_card_detail_returns_404_for_unknown(client: APIClient) -> None:
    resp = client.get(reverse("cards:card-detail", args=[999]))
    assert resp.status_code == status.HTTP_404_NOT_FOUND


# --- printings -----------------------------------------------------------------


@pytest.mark.django_db
def test_printing_list_surfaces_is_multi_variant(client: APIClient) -> None:
    """``is_multi_variant`` flags an ambiguous placeholder (DECISIONS 2026-05-24);
    the matcher uses this to downgrade EXACT→MEDIUM/review and slice 6's UI
    surfaces it. Confirm the flat endpoint exposes it so a slice-4 card-detail view
    can warn the user about a multi-variant generic row."""
    card = _card()
    _printing(card, set_code="A", set_rarity="Common", is_multi_variant=False)
    _printing(card, set_code="B", set_rarity="Common", is_multi_variant=True)

    resp = client.get(reverse("cards:cardprinting-list"))

    assert resp.status_code == status.HTTP_200_OK
    flags = {p["set_code"]: p["is_multi_variant"] for p in resp.data["results"]}
    assert flags == {"A": False, "B": True}


@pytest.mark.django_db
def test_printing_list_filters_by_card_and_set_code(client: APIClient) -> None:
    ash = _card()
    nibiru = _card(name="Nibiru, the Primal Being", passcode=27204311)
    ash_l5dd = _printing(ash, set_code="L5DD-ENC09", set_rarity="Common")
    ash_mama = _printing(ash, set_code="MAMA-EN036", set_rarity="Ultra Rare")
    nibiru_l5dd = _printing(nibiru, set_code="L5DD-ENC10", set_rarity="Common")
    url = reverse("cards:cardprinting-list")

    def ids(resp: Any) -> set[int]:
        return {p["id"] for p in resp.data["results"]}

    assert ids(client.get(url, {"card": ash.pk})) == {ash_l5dd.id, ash_mama.id}
    assert ids(client.get(url, {"set_code": "L5DD-ENC09"})) == {ash_l5dd.id}
    # Mixed int+str values would type as dict[str, object]; stringify the pk so the
    # APIClient.get signature is satisfied without a per-call cast.
    assert ids(client.get(url, {"card": str(ash.pk), "set_code": "MAMA-EN036"})) == {ash_mama.id}
    # No filter → everything.
    assert ids(client.get(url)) == {ash_l5dd.id, ash_mama.id, nibiru_l5dd.id}


@pytest.mark.django_db
def test_printing_list_rejects_invalid_card_id(client: APIClient) -> None:
    resp = client.get(reverse("cards:cardprinting-list"), {"card": "not-an-int"})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_printing_detail_carries_card_name(client: APIClient) -> None:
    card = _card()
    printing = _printing(card)

    resp = client.get(reverse("cards:cardprinting-detail", args=[printing.pk]))

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["id"] == printing.id
    assert resp.data["card_name"] == "Ash Blossom & Joyous Spring"


@pytest.mark.django_db
def test_printing_detail_ignores_query_param_filters(client: APIClient) -> None:
    """Detail actions must not run query-param filtering — a stray ?card= shouldn't 404
    a retrieve via filter_queryset (the imports slice-5 lesson)."""
    card = _card()
    printing = _printing(card)
    other = _card(name="Other", passcode=99999999)

    resp = client.get(reverse("cards:cardprinting-detail", args=[printing.pk]), {"card": other.pk})

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["id"] == printing.id
