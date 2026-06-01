from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class Deck(TimeStampedModel):
    """A named grouping of OWNED holdings — a deck the user tags their cards into.

    Tag/group ONLY (a deck *builder* is a ROADMAP non-goal): a deck references
    ``collection.CollectionItem`` rows through ``DeckMembership``, so it can only ever
    contain cards the user already owns — the moment a deck could reference a not-owned
    card it has crossed the line. Distinct from ``Portfolio`` (the cost-basis / valuation
    accounting boundary, which is part of a holding's natural key): a holding lives in
    exactly one portfolio but can be tagged into any number of decks, so decks are a
    separate, lightweight membership layer that does NOT touch cost basis or valuation.

    Mutable user data (the ``Portfolio`` / ``AlertRule`` posture — created/renamed/deleted
    freely). ``name`` is deliberately NOT unique: it's a user label, not a natural key or
    an FK autocomplete target (the ``AlertRule.name`` precedent, unlike
    ``StorageLocation.name``); an open text label gets no DB CHECK either.
    """

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class DeckMembership(TimeStampedModel):
    """One OWNED holding tagged into one deck — the explicit M2M through-row between
    ``Deck`` and ``collection.CollectionItem`` (the project never uses a bare auto-M2M;
    ``CollectionLot`` / ``AlertEvent`` are the explicit-join precedent).

    Binary in/out: a membership tags the *whole* holding (all copies) into the deck;
    per-copy allocation across decks is deferred exactly like ``CollectionItem``'s
    per-copy ``storage_location`` placement (DECISIONS 2026-05-18). The
    ``(deck, collection_item)`` UNIQUE keeps a holding from being added to the same deck
    twice; both columns are non-null, so it is a plain UNIQUE created AND exercised on
    sqlite (no ``nulls_distinct`` Postgres-only gap — the ``CollectionItem`` natural-key
    precedent).

    Both FKs are ``CASCADE``. ``deck``=CASCADE: a membership is *part of* its deck (the
    ``CollectionLot.collection_item``=CASCADE precedent — delete the deck, its tags go with
    it). ``collection_item``=CASCADE (NOT the ``CollectionItem`` PROTECT-up posture): a
    membership is *derivable tagging*, not cost-basis history, and the OWNED-only invariant
    means a holding the user no longer owns must not linger in a deck — so a holding delete
    silently untags it everywhere rather than raising a ``ProtectedError`` that names
    internal deck tables. (No hard-delete-holding flow exists today, so this is a
    forward-looking choice; flipping ``collection_item`` to PROTECT is a one-line migration
    if a future "you'd lose these deck tags" confirmation is wanted instead.)
    """

    deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name="memberships")
    collection_item = models.ForeignKey(
        "collection.CollectionItem",
        on_delete=models.CASCADE,
        related_name="deck_memberships",
    )

    class Meta:
        # Stable order for the paginated membership feed; ``id`` is the deterministic
        # tiebreaker within a deck (the CollectionLot/SyncRun ordering lesson).
        ordering = ["deck", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["deck", "collection_item"],
                name="unique_deck_membership",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.collection_item} in {self.deck}"
