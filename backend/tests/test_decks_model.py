from __future__ import annotations

import pytest
from django.db import IntegrityError

from apps.cards.models import Card, CardPrinting
from apps.collection.models import CollectionItem, Condition, Language
from apps.core.enums import Edition
from apps.decks.models import Deck, DeckMembership
from apps.portfolio.models import Portfolio


def _item(*, card_name: str = "Ash Blossom & Joyous Spring", set_code: str = "L5DD-ENC09") -> CollectionItem:
    # Each holding gets its own card so the CardPrinting natural key never collides
    # across backends (the (card, ...) leg differs).
    card = Card.objects.create(name=card_name)
    printing = CardPrinting.objects.create(
        card=card, set_code=set_code, set_rarity="Common", set_name="set"
    )
    portfolio = Portfolio.objects.get_or_create(name="Yubel Deck")[0]
    return CollectionItem.objects.create(
        portfolio=portfolio,
        printing=printing,
        condition=Condition.NEAR_MINT,
        edition=Edition.FIRST_EDITION,
        language=Language.ENGLISH,
    )


@pytest.mark.django_db
def test_deck_str_is_name() -> None:
    assert str(Deck.objects.create(name="Snake-Eye")) == "Snake-Eye"


@pytest.mark.django_db
def test_membership_str_mentions_deck() -> None:
    deck = Deck.objects.create(name="Snake-Eye")
    membership = DeckMembership.objects.create(deck=deck, collection_item=_item())
    assert "Snake-Eye" in str(membership)


@pytest.mark.django_db
def test_membership_is_unique_per_deck_and_holding() -> None:
    """The (deck, collection_item) UNIQUE — both columns non-null, so a plain UNIQUE
    created AND exercised on sqlite (no Postgres-only nulls_distinct gap)."""
    deck = Deck.objects.create(name="Snake-Eye")
    item = _item()
    DeckMembership.objects.create(deck=deck, collection_item=item)
    with pytest.raises(IntegrityError):
        DeckMembership.objects.create(deck=deck, collection_item=item)


@pytest.mark.django_db
def test_a_holding_can_belong_to_multiple_decks() -> None:
    item = _item()
    DeckMembership.objects.create(deck=Deck.objects.create(name="Snake-Eye"), collection_item=item)
    DeckMembership.objects.create(deck=Deck.objects.create(name="Fire King"), collection_item=item)
    assert item.deck_memberships.count() == 2


@pytest.mark.django_db
def test_deleting_a_deck_cascades_its_memberships_but_not_the_holding() -> None:
    deck = Deck.objects.create(name="Snake-Eye")
    item = _item()
    DeckMembership.objects.create(deck=deck, collection_item=item)

    deck.delete()

    assert DeckMembership.objects.count() == 0
    assert CollectionItem.objects.filter(pk=item.pk).exists()  # the holding survives


@pytest.mark.django_db
def test_deleting_a_holding_cascades_its_memberships_but_not_the_deck() -> None:
    """``collection_item`` is CASCADE (NOT the CollectionItem PROTECT-up posture): a
    holding the user no longer owns must not linger in a deck (the OWNED-only invariant),
    so deleting it untags it everywhere rather than raising a ProtectedError naming
    internal deck tables — the deliberate divergence recorded in DECISIONS 2026-05-31."""
    deck = Deck.objects.create(name="Snake-Eye")
    item = _item()
    DeckMembership.objects.create(deck=deck, collection_item=item)

    item.delete()

    assert DeckMembership.objects.count() == 0
    assert Deck.objects.filter(pk=deck.pk).exists()  # the deck survives
