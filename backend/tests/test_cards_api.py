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
    """An authenticated APIClient. Every viewset inherits the DRF default
    ``IsAuthenticated``, so anonymous calls 403 (the same posture as the imports API
    and the schema-permissions rule in invariant 7 in ARCHITECTURE.md)."""
    user = get_user_model().objects.create_user("reader", "r@example.com", "x")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _card(
    name: str = "Ash Blossom & Joyous Spring",
    passcode: int | None = 14558127,
    *,
    archetype: str | None = None,
) -> Card:
    return Card.objects.create(name=name, passcode=passcode, archetype=archetype)


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
        "archetype": None,
        "printings_count": 0,
    }
    assert second == {
        "id": nibiru.id,
        "passcode": 27204311,
        "name": "Nibiru, the Primal Being",
        "archetype": None,
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
    """``is_multi_variant`` flags an ambiguous placeholder. The matcher uses this
    to downgrade EXACT to MEDIUM/review and the import UI surfaces it. Confirm the
    flat endpoint exposes it so a card-detail view can warn the user about a
    multi-variant generic row."""
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
    """Detail actions must not run query-param filtering: a stray ?card= shouldn't 404
    a retrieve via filter_queryset (the same lesson learned on the imports API)."""
    card = _card()
    printing = _printing(card)
    other = _card(name="Other", passcode=99999999)

    resp = client.get(reverse("cards:cardprinting-detail", args=[printing.pk]), {"card": other.pk})

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["id"] == printing.id


# --- card name search (slice 6 override picker) ---------------------------------


@pytest.mark.django_db
def test_card_list_search_filters_by_name_case_insensitively(client: APIClient) -> None:
    """?search= is a case-insensitive substring on name. The override picker finds a card
    by name, then lists its printings via ?card=."""
    _card(name="Ash Blossom & Joyous Spring", passcode=14558127)
    _card(name="Ghost Ogre & Snow Rabbit", passcode=59438930)
    _card(name="Dark Magician", passcode=46986414)

    resp = client.get(reverse("cards:card-list"), {"search": "blossom"})

    assert resp.status_code == status.HTTP_200_OK
    names = [row["name"] for row in resp.data["results"]]
    assert names == ["Ash Blossom & Joyous Spring"]


@pytest.mark.django_db
def test_card_list_blank_search_returns_all(client: APIClient) -> None:
    """A cleared search box sends ?search=: treat empty/whitespace as 'no filter', not
    'match nothing'."""
    _card(name="Ash Blossom & Joyous Spring", passcode=14558127)
    _card(name="Dark Magician", passcode=46986414)

    resp = client.get(reverse("cards:card-list"), {"search": "   "})

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["count"] == 2


@pytest.mark.django_db
def test_card_detail_ignores_search_param(client: APIClient) -> None:
    """Detail must not run the list-only search filter: a stray ?search= shouldn't 404 a
    retrieve via filter_queryset (the list-only-guard convention)."""
    card = _card(name="Dark Magician", passcode=46986414)

    resp = client.get(reverse("cards:card-detail", args=[card.pk]), {"search": "blossom"})

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["id"] == card.pk


# --- archetype (Phase 5) --------------------------------------------------------


@pytest.mark.django_db
def test_card_list_includes_archetype(client: APIClient) -> None:
    """archetype is surfaced on the list (the Phase 5 /cards column). A card with no
    archetype reads ``null``, never an empty string (NULL is the canonical 'none')."""
    _card(name="Blue-Eyes White Dragon", passcode=89631139, archetype="Blue-Eyes")
    _card(name="Pot of Greed", passcode=55144522)  # no archetype

    resp = client.get(reverse("cards:card-list"))

    assert resp.status_code == status.HTTP_200_OK
    by_name = {row["name"]: row["archetype"] for row in resp.data["results"]}
    assert by_name == {"Blue-Eyes White Dragon": "Blue-Eyes", "Pot of Greed": None}


@pytest.mark.django_db
def test_card_list_filters_by_archetype_exact(client: APIClient) -> None:
    """?archetype= is an EXACT match (a facet, not a substring): 'Blue' matches nothing."""
    blue = _card(name="Blue-Eyes White Dragon", passcode=89631139, archetype="Blue-Eyes")
    _card(name="Pot of Greed", passcode=55144522)  # no archetype
    striker = _card(name="Sky Striker Ace - Roze", passcode=26077387, archetype="Sky Striker")
    url = reverse("cards:card-list")

    def ids(resp: Any) -> set[int]:
        return {row["id"] for row in resp.data["results"]}

    assert ids(client.get(url, {"archetype": "Blue-Eyes"})) == {blue.id}
    assert ids(client.get(url, {"archetype": "Sky Striker"})) == {striker.id}
    assert client.get(url, {"archetype": "Blue"}).data["count"] == 0  # exact, not prefix


@pytest.mark.django_db
def test_card_list_blank_archetype_returns_all(client: APIClient) -> None:
    """A cleared dropdown sends ?archetype=: empty/whitespace is 'no filter', not
    'match the empty archetype'."""
    _card(name="Blue-Eyes White Dragon", passcode=89631139, archetype="Blue-Eyes")
    _card(name="Pot of Greed", passcode=55144522)

    resp = client.get(reverse("cards:card-list"), {"archetype": "  "})

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["count"] == 2


@pytest.mark.django_db
def test_card_detail_ignores_archetype_param(client: APIClient) -> None:
    """Detail must not run the list-only archetype filter (the list-only-guard convention)."""
    card = _card(name="Pot of Greed", passcode=55144522)  # archetype None

    resp = client.get(reverse("cards:card-detail", args=[card.pk]), {"archetype": "Blue-Eyes"})

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["id"] == card.pk
    assert resp.data["archetype"] is None


@pytest.mark.django_db
def test_archetypes_action_lists_distinct_sorted_excluding_null(client: APIClient) -> None:
    """The filter-dropdown source: distinct non-null archetypes, sorted; duplicates
    collapse and cards without an archetype contribute nothing."""
    _card(name="Sky Striker Ace - Roze", passcode=26077387, archetype="Sky Striker")
    _card(name="Sky Striker Ace - Raye", passcode=63288573, archetype="Sky Striker")  # dup
    _card(name="Blue-Eyes White Dragon", passcode=89631139, archetype="Blue-Eyes")
    _card(name="Pot of Greed", passcode=55144522)  # no archetype

    resp = client.get(reverse("cards:card-archetypes"))

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data == ["Blue-Eyes", "Sky Striker"]


@pytest.mark.django_db
def test_archetypes_action_requires_authentication() -> None:
    anon = APIClient()
    assert anon.get(reverse("cards:card-archetypes")).status_code == status.HTTP_403_FORBIDDEN
