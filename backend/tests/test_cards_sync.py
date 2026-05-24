from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.cards.models import Card, CardPrinting, MetadataSource, PrintingAlias
from apps.cards.sync import run_ygoprodeck_sync, sync_cards_from_metadata
from apps.core.models import SyncKind, SyncRun, SyncStatus
from apps.core.sync_history import record_run
from apps.pricing.providers.base import (
    CardMetadata,
    JsonFetcher,
    MetadataProvider,
    PrintingMetadata,
)


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
def test_sync_resolves_through_alias_after_reconciliation() -> None:
    """Once TCGCSV reconciliation has corrected a provisional rarity and recorded a
    PrintingAlias, a YGOPRODeck re-sync of the *original* provisional key resolves to
    the canonical printing instead of recreating the provisional duplicate — the
    round-4 rerun-safety prerequisite (DECISIONS 2026-05-23)."""
    card = Card.objects.create(passcode=24094653, name="Super Polymerization")
    canonical = CardPrinting.objects.create(
        card=card,
        set_code="RA03-EN053",
        set_rarity="Prismatic Ultimate Rare",  # already canonicalized by reconciliation
        set_name="Quarter Century Stampede",
    )
    PrintingAlias.objects.create(
        source=MetadataSource.YGOPRODECK,
        card=card,
        set_code="RA03-EN053",
        set_rarity="Ultimate Rare",  # the provisional key YGOPRODeck still emits
        printing=canonical,
    )
    record = CardMetadata(
        passcode=24094653,
        name="Super Polymerization",
        printings=(PrintingMetadata("RA03-EN053", "Ultimate Rare", "Quarter Century Stampede"),),
    )

    result = sync_cards_from_metadata(FakeMetadataProvider([record]))

    assert result.printings_created == 0  # resolved via alias, not recreated
    assert CardPrinting.objects.count() == 1  # no provisional duplicate
    assert CardPrinting.objects.get().set_rarity == "Prismatic Ultimate Rare"


@pytest.mark.django_db
def test_management_command_syncs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The command runs the guarded orchestration (run_ygoprodeck_sync); here the
    provider is swapped at the orchestration's construction site for a fake (ignoring
    the injected fetch/min_cards) so no network call happens."""
    monkeypatch.setattr(
        "apps.cards.sync.YgoprodeckProvider", lambda *a, **k: FakeMetadataProvider([_BLUE_EYES])
    )

    call_command("sync_ygoprodeck")

    assert Card.objects.filter(passcode=89631139).exists()
    assert CardPrinting.objects.count() == 2
    # The orchestration records a SUCCESS run for the next sync's baseline.
    assert SyncRun.objects.filter(
        kind=SyncKind.YGOPRODECK_METADATA, status=SyncStatus.SUCCESS
    ).exists()


# --- run_ygoprodeck_sync: cardinality guard + history recording -------------


def _ygo_payload(cards: list[tuple[int, str, list[tuple[str, str]]]]) -> dict[str, object]:
    """A YGOPRODeck cardinfo.php payload from (passcode, name, [(set_code, rarity)]) tuples."""
    return {
        "data": [
            {
                "id": passcode,
                "name": name,
                "card_sets": [
                    {"set_code": sc, "set_rarity": sr, "set_name": "Some Set"} for sc, sr in sets
                ],
            }
            for passcode, name, sets in cards
        ]
    }


def _fetch(payload: dict[str, object]) -> JsonFetcher:
    return lambda _url: payload


@pytest.mark.django_db
def test_run_ygoprodeck_sync_records_success_with_cardinality() -> None:
    # Seed a tiny baseline so the dynamic floor (≈card_count * 0.98) sits below the
    # fixture; with no history the ~1000-card bootstrap floor would reject it.
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=2)
    payload = _ygo_payload(
        [
            (1, "Card A", [("AA-001", "Common")]),
            (2, "Card B", [("BB-001", "Rare"), ("BB-002", "Common")]),
            (3, "Card C", []),
        ]
    )

    result = run_ygoprodeck_sync(fetch=_fetch(payload))

    assert result is not None
    assert result.cards_created == 3
    assert result.printings_created == 3
    new_run = SyncRun.objects.get(kind=SyncKind.YGOPRODECK_METADATA, card_count=3)
    assert new_run.status == SyncStatus.SUCCESS
    assert new_run.printing_count == 3
    assert new_run.detail["cards_created"] == 3


@pytest.mark.django_db
def test_run_ygoprodeck_sync_guard_rejects_shrunk_fetch() -> None:
    """A fetch below last_good * (1 - tolerance) is rejected before any write, and the
    rejection is recorded FAILED (so it never becomes the baseline)."""
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=1000)
    payload = _ygo_payload([(i, f"Card {i}", []) for i in range(1, 4)])  # 3 ≪ floor 980

    with pytest.raises(ValueError, match="floor"):
        run_ygoprodeck_sync(fetch=_fetch(payload))

    assert Card.objects.count() == 0  # rejected at fetch, before the write loop
    failed = SyncRun.objects.get(kind=SyncKind.YGOPRODECK_METADATA, status=SyncStatus.FAILED)
    assert "floor" in failed.error


@pytest.mark.django_db
def test_run_ygoprodeck_sync_first_run_enforces_bootstrap_floor() -> None:
    """No history → the provider's absolute bootstrap floor (~1000) still guards run 1."""
    with pytest.raises(ValueError, match="floor"):
        run_ygoprodeck_sync(fetch=_fetch(_ygo_payload([(1, "Only Card", [])])))

    assert (
        SyncRun.objects.filter(
            kind=SyncKind.YGOPRODECK_METADATA, status=SyncStatus.FAILED
        ).count()
        == 1
    )


@pytest.mark.django_db
@override_settings(SYNC_GUARD_METADATA_TOLERANCE=2.0)
def test_run_ygoprodeck_sync_rejects_misconfigured_tolerance() -> None:
    """A percent-style tolerance (=2 for "2%") would push the floor non-positive and
    silently disable the guard; the sync fails closed instead and records FAILED
    (adversarial-review F1)."""
    with pytest.raises(ValueError, match="tolerance"):
        run_ygoprodeck_sync(fetch=_fetch(_ygo_payload([(1, "A", [])])))

    assert Card.objects.count() == 0  # rejected before any fetch/write
    assert SyncRun.objects.filter(
        kind=SyncKind.YGOPRODECK_METADATA, status=SyncStatus.FAILED
    ).exists()


@pytest.mark.django_db
def test_run_ygoprodeck_sync_skips_when_lock_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """If another run holds the advisory lock, this one skips: returns None, records no
    SyncRun, and writes nothing (adversarial-review F2)."""

    @contextmanager
    def _held(_kind: object) -> Iterator[bool]:
        yield False

    monkeypatch.setattr("apps.cards.sync.sync_lock", _held)

    result = run_ygoprodeck_sync(fetch=_fetch(_ygo_payload([(1, "A", [])])))

    assert result is None
    assert not SyncRun.objects.exists()
    assert not Card.objects.exists()


@pytest.mark.django_db
def test_management_command_reports_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the orchestration skips (lock held), the command says so rather than
    crashing on a None result."""
    monkeypatch.setattr(
        "apps.cards.management.commands.sync_ygoprodeck.run_ygoprodeck_sync", lambda: None
    )
    out = StringIO()

    call_command("sync_ygoprodeck", stdout=out)

    assert "skipped" in out.getvalue().lower()
