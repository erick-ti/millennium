from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.cards.models import Card, CardPrinting
from apps.collection.models import CollectionItem, CollectionLot, Condition, Language
from apps.core.enums import Edition
from apps.decks.models import Deck, DeckMembership
from apps.portfolio.models import Portfolio


@pytest.fixture
def client() -> APIClient:
    user = get_user_model().objects.create_user("reader", "r@example.com", "x")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _item(
    *,
    card_name: str = "Ash Blossom & Joyous Spring",
    set_code: str = "L5DD-ENC09",
    set_rarity: str = "Common",
    portfolio_name: str = "Yubel Deck",
    condition: Condition = Condition.NEAR_MINT,
    edition: Edition = Edition.FIRST_EDITION,
    language: Language = Language.ENGLISH,
) -> CollectionItem:
    card = Card.objects.create(name=card_name)
    printing = CardPrinting.objects.create(
        card=card, set_code=set_code, set_rarity=set_rarity, set_name="set"
    )
    portfolio = Portfolio.objects.get_or_create(name=portfolio_name)[0]
    return CollectionItem.objects.create(
        portfolio=portfolio,
        printing=printing,
        condition=condition,
        edition=edition,
        language=language,
    )


def _lot(item: CollectionItem, *, quantity: int = 1) -> CollectionLot:
    return CollectionLot.objects.create(collection_item=item, quantity=quantity)


# --- auth ----------------------------------------------------------------------


@pytest.mark.django_db
def test_endpoints_require_authentication() -> None:
    anon = APIClient()
    assert anon.get(reverse("decks:deck-list")).status_code == 403
    assert anon.get(reverse("decks:deckmembership-list")).status_code == 403


# --- deck CRUD -----------------------------------------------------------------


@pytest.mark.django_db
def test_create_deck(client: APIClient) -> None:
    resp = client.post(
        reverse("decks:deck-list"), {"name": "  Snake-Eye  ", "description": "Tier 1"}, format="json"
    )
    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.data["name"] == "Snake-Eye"  # trimmed at the boundary
    assert resp.data["description"] == "Tier 1"
    assert resp.data["member_count"] == 0  # annotation-safe on a fresh instance


@pytest.mark.django_db
def test_create_deck_rejects_blank_name(client: APIClient) -> None:
    resp = client.post(reverse("decks:deck-list"), {"name": "   "}, format="json")
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "name" in resp.data


@pytest.mark.django_db
def test_list_decks_carries_member_count(client: APIClient) -> None:
    deck = Deck.objects.create(name="Snake-Eye")
    DeckMembership.objects.create(deck=deck, collection_item=_item())
    DeckMembership.objects.create(deck=deck, collection_item=_item(set_code="ROTA-EN001"))

    resp = client.get(reverse("decks:deck-list"))

    assert resp.status_code == status.HTTP_200_OK
    [row] = resp.data["results"]
    assert row["name"] == "Snake-Eye"
    assert row["member_count"] == 2


@pytest.mark.django_db
def test_retrieve_deck(client: APIClient) -> None:
    deck = Deck.objects.create(name="Snake-Eye")
    DeckMembership.objects.create(deck=deck, collection_item=_item())

    resp = client.get(reverse("decks:deck-detail", args=[deck.pk]))

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["member_count"] == 1


@pytest.mark.django_db
def test_rename_deck(client: APIClient) -> None:
    deck = Deck.objects.create(name="Snake-Eye")
    resp = client.patch(
        reverse("decks:deck-detail", args=[deck.pk]), {"name": "Snake-Eye Unchained"}, format="json"
    )
    assert resp.status_code == status.HTTP_200_OK
    deck.refresh_from_db()
    assert deck.name == "Snake-Eye Unchained"


@pytest.mark.django_db
def test_rename_trims_the_name(client: APIClient) -> None:
    """validate_name runs on PATCH too (DRF runs field validators on partial_update for
    any provided field) — so a rename trims, not just create."""
    deck = Deck.objects.create(name="Snake-Eye")
    resp = client.patch(
        reverse("decks:deck-detail", args=[deck.pk]), {"name": "  Renamed  "}, format="json"
    )
    assert resp.status_code == status.HTTP_200_OK
    deck.refresh_from_db()
    assert deck.name == "Renamed"


@pytest.mark.django_db
def test_rename_rejects_a_blank_name(client: APIClient) -> None:
    deck = Deck.objects.create(name="Snake-Eye")
    resp = client.patch(
        reverse("decks:deck-detail", args=[deck.pk]), {"name": "   "}, format="json"
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "name" in resp.data


@pytest.mark.django_db
def test_delete_deck(client: APIClient) -> None:
    deck = Deck.objects.create(name="Snake-Eye")
    DeckMembership.objects.create(deck=deck, collection_item=_item())

    resp = client.delete(reverse("decks:deck-detail", args=[deck.pk]))

    assert resp.status_code == status.HTTP_204_NO_CONTENT
    assert not Deck.objects.filter(pk=deck.pk).exists()
    assert DeckMembership.objects.count() == 0  # cascades


# --- membership add / remove ---------------------------------------------------


@pytest.mark.django_db
def test_add_member_returns_denormalized_holding_identity(client: APIClient) -> None:
    deck = Deck.objects.create(name="Snake-Eye")
    item = _item()
    _lot(item, quantity=1)  # a deck only accepts a held (quantity > 0) holding

    resp = client.post(
        reverse("decks:deckmembership-list"),
        {"deck": deck.pk, "collection_item": item.pk},
        format="json",
    )

    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.data["deck"] == deck.pk
    assert resp.data["collection_item"] == item.pk
    assert resp.data["card_name"] == "Ash Blossom & Joyous Spring"
    assert resp.data["set_code"] == "L5DD-ENC09"
    assert resp.data["set_rarity"] == "Common"
    assert resp.data["variant_label"] is None  # a no-variant printing — nullable field
    assert resp.data["condition"] == Condition.NEAR_MINT
    assert resp.data["edition"] == Edition.FIRST_EDITION
    assert resp.data["language"] == Language.ENGLISH
    assert resp.data["portfolio_name"] == "Yubel Deck"
    assert resp.data["quantity"] == 1


@pytest.mark.django_db
def test_cannot_add_a_zero_copy_holding(client: APIClient) -> None:
    """A deck groups cards you HOLD, so a lot-less (quantity 0) holding is rejected at the API
    boundary with a clean 400 — even though the CollectionItem row exists (Codex adversarial
    review 2026-05-31). Defense-in-depth behind the picker's own quantity>0 filter."""
    deck = Deck.objects.create(name="Snake-Eye")
    item = _item()  # no lots → quantity 0

    resp = client.post(
        reverse("decks:deckmembership-list"),
        {"deck": deck.pk, "collection_item": item.pk},
        format="json",
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "collection_item" in resp.data
    assert not DeckMembership.objects.exists()


@pytest.mark.django_db
def test_member_row_reports_holding_copy_count(client: APIClient) -> None:
    """A holding tagged into a deck is ONE membership (member_count counts holdings), but the
    row carries the holding's copy count — the SUM of its lots (the CollectionItem.quantity
    definition) — so the member table can show that one holding is N physical copies."""
    deck = Deck.objects.create(name="Snake-Eye")
    item = _item()
    CollectionLot.objects.create(collection_item=item, quantity=2)
    CollectionLot.objects.create(collection_item=item, quantity=1)
    DeckMembership.objects.create(deck=deck, collection_item=item)

    resp = client.get(reverse("decks:deckmembership-list"), {"deck": deck.pk})

    assert resp.status_code == status.HTTP_200_OK
    [row] = resp.data["results"]
    assert row["quantity"] == 3  # SUM of the two lots, not the membership count (which is 1)


@pytest.mark.django_db
def test_add_member_201_carries_the_copy_count(client: APIClient) -> None:
    """The 201 re-fetches through the annotated queryset, so the create response carries the
    holding's quantity even though the freshly created row has no annotation."""
    deck = Deck.objects.create(name="Snake-Eye")
    item = _item()
    CollectionLot.objects.create(collection_item=item, quantity=4)

    resp = client.post(
        reverse("decks:deckmembership-list"),
        {"deck": deck.pk, "collection_item": item.pk},
        format="json",
    )

    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.data["quantity"] == 4


@pytest.mark.django_db
def test_add_duplicate_member_returns_409(client: APIClient) -> None:
    """A holding already in the deck → a clean 409 (informative; the import 409 the
    frontend already reads), never a second row."""
    deck = Deck.objects.create(name="Snake-Eye")
    item = _item()
    _lot(item, quantity=1)
    body = {"deck": deck.pk, "collection_item": item.pk}

    first = client.post(reverse("decks:deckmembership-list"), body, format="json")
    second = client.post(reverse("decks:deckmembership-list"), body, format="json")

    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_409_CONFLICT
    assert DeckMembership.objects.filter(deck=deck, collection_item=item).count() == 1


@pytest.mark.django_db
def test_add_member_with_unknown_holding_is_400(client: APIClient) -> None:
    """OWNED-only is structural: you can only POST a CollectionItem id, and an unknown id
    (a card that isn't an owned holding) is a clean 400, not a 500."""
    deck = Deck.objects.create(name="Snake-Eye")
    resp = client.post(
        reverse("decks:deckmembership-list"),
        {"deck": deck.pk, "collection_item": 999_999},
        format="json",
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "collection_item" in resp.data


@pytest.mark.django_db
def test_remove_member(client: APIClient) -> None:
    deck = Deck.objects.create(name="Snake-Eye")
    membership = DeckMembership.objects.create(deck=deck, collection_item=_item())

    resp = client.delete(reverse("decks:deckmembership-detail", args=[membership.pk]))

    assert resp.status_code == status.HTTP_204_NO_CONTENT
    assert not DeckMembership.objects.filter(pk=membership.pk).exists()


# --- membership feed / filter --------------------------------------------------


@pytest.mark.django_db
def test_membership_list_filters_by_deck(client: APIClient) -> None:
    deck_a = Deck.objects.create(name="Snake-Eye")
    deck_b = Deck.objects.create(name="Fire King")
    DeckMembership.objects.create(deck=deck_a, collection_item=_item())
    DeckMembership.objects.create(deck=deck_b, collection_item=_item(set_code="ROTA-EN001"))

    resp = client.get(reverse("decks:deckmembership-list"), {"deck": deck_a.pk})

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["count"] == 1
    assert resp.data["results"][0]["deck"] == deck_a.pk


@pytest.mark.django_db
def test_membership_list_rejects_non_integer_deck_filter(client: APIClient) -> None:
    resp = client.get(reverse("decks:deckmembership-list"), {"deck": "abc"})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "deck" in resp.data
