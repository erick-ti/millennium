from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.core.management import call_command

from apps.cards.models import Card, CardPrinting
from apps.cards.sync import sync_cards_from_metadata
from apps.pricing.providers.base import CardMetadata, MetadataProvider, PrintingMetadata


class FakeMetadataProvider(MetadataProvider):
    """Yields a fixed list of records — no network, no HTTP."""

    def __init__(self, records: list[CardMetadata]) -> None:
        self._records = records

    def fetch_card_metadata(self) -> Iterator[CardMetadata]:
        yield from self._records


_BLUE_EYES = CardMetadata(
    passcode=89631139,
    name="Blue-Eyes White Dragon",
    printings=(
        PrintingMetadata("LOB-001", "Ultra Rare", "Legend of Blue Eyes White Dragon"),
        PrintingMetadata("SDK-001", "Ultra Rare", "Starter Deck: Kaiba"),
    ),
)
_DARK_MAGICIAN = CardMetadata(
    passcode=46986414,
    name="Dark Magician",
    printings=(PrintingMetadata("LOB-005", "Ultra Rare", "Legend of Blue Eyes White Dragon"),),
)


@pytest.mark.django_db
def test_sync_creates_cards_and_printings() -> None:
    result = sync_cards_from_metadata(FakeMetadataProvider([_BLUE_EYES, _DARK_MAGICIAN]))

    assert result.cards_created == 2
    assert result.printings_created == 3
    assert Card.objects.count() == 2
    assert CardPrinting.objects.count() == 3


@pytest.mark.django_db
def test_sync_derives_normalized_name() -> None:
    """Upserts go through save(), so normalized_name is derived (not bypassed)."""
    sync_cards_from_metadata(FakeMetadataProvider([_BLUE_EYES]))

    card = Card.objects.get(passcode=89631139)
    assert card.normalized_name == "blue-eyes white dragon"


@pytest.mark.django_db
def test_sync_is_idempotent() -> None:
    provider = FakeMetadataProvider([_BLUE_EYES, _DARK_MAGICIAN])
    sync_cards_from_metadata(provider)
    card = Card.objects.get(passcode=89631139)
    first_updated_at = card.updated_at

    second = sync_cards_from_metadata(provider)

    # Unchanged rows are reported as unchanged and not rewritten.
    assert second.cards_created == 0
    assert second.cards_updated == 0
    assert second.cards_unchanged == 2
    assert second.printings_created == 0
    assert second.printings_updated == 0
    assert second.printings_unchanged == 3
    assert Card.objects.count() == 2
    assert CardPrinting.objects.count() == 3

    # The hot path performs no writes, so updated_at stays a real change signal.
    card.refresh_from_db()
    assert card.updated_at == first_updated_at


@pytest.mark.django_db
def test_sync_updates_changed_name() -> None:
    sync_cards_from_metadata(FakeMetadataProvider([_DARK_MAGICIAN]))
    renamed = CardMetadata(passcode=46986414, name="Dark Magician (errata)")

    result = sync_cards_from_metadata(FakeMetadataProvider([renamed]))

    assert result.cards_updated == 1
    assert result.cards_unchanged == 0
    card = Card.objects.get(passcode=46986414)
    assert card.name == "Dark Magician (errata)"
    assert card.normalized_name == "dark magician (errata)"
    assert Card.objects.count() == 1


@pytest.mark.django_db
def test_sync_card_without_printings() -> None:
    unreleased = CardMetadata(passcode=999, name="Unreleased Card")

    result = sync_cards_from_metadata(FakeMetadataProvider([unreleased]))

    assert result.cards_created == 1
    assert result.printings_created == 0
    assert CardPrinting.objects.count() == 0


@pytest.mark.django_db
def test_management_command_syncs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The command wires the real provider to the sync service; here the provider
    is swapped for a fake so no network call happens."""
    from apps.cards.management.commands import sync_ygoprodeck as command_module

    monkeypatch.setattr(
        command_module, "YgoprodeckProvider", lambda: FakeMetadataProvider([_BLUE_EYES])
    )

    call_command("sync_ygoprodeck")

    assert Card.objects.filter(passcode=89631139).exists()
    assert CardPrinting.objects.count() == 2
