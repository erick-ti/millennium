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
    """Yields a fixed list of records, no network, no HTTP."""

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
def test_sync_creates_card_with_archetype() -> None:
    """Phase 5: a created card persists the provider's archetype."""
    record = CardMetadata(passcode=89631139, name="Blue-Eyes White Dragon", archetype="Blue-Eyes")

    result = sync_cards_from_metadata(FakeMetadataProvider([record]))

    assert result.cards_created == 1
    assert Card.objects.get(passcode=89631139).archetype == "Blue-Eyes"


@pytest.mark.django_db
def test_sync_updates_changed_archetype() -> None:
    """An archetype change (added/corrected/withdrawn) is a real metadata update: it
    counts and writes like a rename, so the next scheduled sync backfills existing cards."""
    sync_cards_from_metadata(FakeMetadataProvider([CardMetadata(passcode=1, name="X")]))  # None
    tagged = CardMetadata(passcode=1, name="X", archetype="Newly Tagged")

    result = sync_cards_from_metadata(FakeMetadataProvider([tagged]))

    assert result.cards_updated == 1
    assert result.cards_unchanged == 0
    assert Card.objects.get(passcode=1).archetype == "Newly Tagged"


@pytest.mark.django_db
def test_sync_unchanged_when_archetype_same() -> None:
    """An unchanged archetype performs no write (the idempotency contract: updated_at
    stays meaningful)."""
    record = CardMetadata(passcode=1, name="X", archetype="Stable")
    sync_cards_from_metadata(FakeMetadataProvider([record]))

    result = sync_cards_from_metadata(FakeMetadataProvider([record]))

    assert result.cards_updated == 0
    assert result.cards_unchanged == 1


def _seed_tagged_cards(n: int, archetype: str = "Blue-Eyes") -> None:
    """Seed ``n`` already-archetyped cards (bulk_create bypasses save(), so
    normalized_name is set explicitly; the withdrawal guard only reads passcode/archetype)."""
    Card.objects.bulk_create(
        Card(passcode=i, name=f"Card {i}", normalized_name=f"card {i}", archetype=archetype)
        for i in range(1, n + 1)
    )


@pytest.mark.django_db
def test_sync_rejects_mass_archetype_withdrawal_before_writing() -> None:
    """The withdrawal guard is a pure pre-write check: a fetch that would null archetype
    on many currently-tagged cards raises BEFORE the write loop, so the tags survive."""
    _seed_tagged_cards(40)
    # A re-fetch of all 40 with the archetype key gone → 40 withdrawals.
    records = [CardMetadata(passcode=i, name=f"Card {i}") for i in range(1, 41)]

    with pytest.raises(ValueError, match="archetype"):
        sync_cards_from_metadata(FakeMetadataProvider(records), archetype_withdrawal_tolerance=0.05)

    assert Card.objects.exclude(archetype__isnull=True).count() == 40  # nothing nulled


@pytest.mark.django_db
def test_sync_allows_small_archetype_correction() -> None:
    """A handful of legit withdrawals (below the absolute floor) are applied, not blocked:
    the guard targets mass loss, not routine errata."""
    _seed_tagged_cards(40)
    # 37 unchanged (still tagged) + 3 legitimate withdrawals.
    records = [
        CardMetadata(passcode=i, name=f"Card {i}", archetype="Blue-Eyes" if i > 3 else None)
        for i in range(1, 41)
    ]

    result = sync_cards_from_metadata(FakeMetadataProvider(records), archetype_withdrawal_tolerance=0.05)

    assert result.cards_updated == 3  # the 3 withdrawals applied
    assert Card.objects.exclude(archetype__isnull=True).count() == 37


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
    the canonical printing instead of recreating the provisional duplicate, satisfying
    the rerun-safety prerequisite for the metadata sync to run repeatedly without
    duplicating rows."""
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
def test_run_ygoprodeck_sync_records_archetype_coverage_telemetry() -> None:
    """A SUCCESS run records its non-null archetype count in the SyncRun detail (coverage
    telemetry). With no currently-tagged cards there are no withdrawals, so the run isn't
    blocked."""
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=2)  # clears card floor
    payload: dict[str, object] = {
        "data": [
            {"id": 1, "name": "Blue-Eyes White Dragon", "archetype": "Blue-Eyes", "card_sets": []},
            {"id": 2, "name": "Kaibaman", "archetype": "Blue-Eyes", "card_sets": []},
            {"id": 3, "name": "Pot of Greed", "card_sets": []},  # no archetype
        ]
    }

    result = run_ygoprodeck_sync(fetch=_fetch(payload))

    assert result is not None
    assert result.archetype_count == 2
    run = SyncRun.objects.get(
        kind=SyncKind.YGOPRODECK_METADATA, status=SyncStatus.SUCCESS, card_count=3
    )
    assert run.detail["archetype_count"] == 2


@pytest.mark.django_db
def test_run_ygoprodeck_sync_rejects_partial_archetype_withdrawal() -> None:
    """A PARTIAL key-drop that leaves >50% of cards tagged (so an
    aggregate count floor would pass) is still rejected by the withdrawal guard, recorded
    FAILED, and every existing tag survives."""
    record_run(SyncKind.YGOPRODECK_METADATA, SyncStatus.SUCCESS, card_count=60)  # clears card floor
    _seed_tagged_cards(60)
    # All 60 cards still present (clears the card floor) but archetype dropped for 30: 30
    # remain tagged, which a 50% aggregate-count floor would have let through. (At n=60 the
    # absolute floor of 25 is the active threshold; 30 > 25.)
    payload: dict[str, object] = {
        "data": [
            {
                "id": i,
                "name": f"Card {i}",
                "card_sets": [],
                **({"archetype": "Blue-Eyes"} if i > 30 else {}),
            }
            for i in range(1, 61)
        ]
    }

    with pytest.raises(ValueError, match="archetype"):
        run_ygoprodeck_sync(fetch=_fetch(payload))

    assert Card.objects.exclude(archetype__isnull=True).count() == 60  # all tags intact
    failed = SyncRun.objects.get(kind=SyncKind.YGOPRODECK_METADATA, status=SyncStatus.FAILED)
    assert "archetype" in failed.error


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
    silently disable the guard; the sync fails closed instead and records FAILED."""
    with pytest.raises(ValueError, match="tolerance"):
        run_ygoprodeck_sync(fetch=_fetch(_ygo_payload([(1, "A", [])])))

    assert Card.objects.count() == 0  # rejected before any fetch/write
    assert SyncRun.objects.filter(
        kind=SyncKind.YGOPRODECK_METADATA, status=SyncStatus.FAILED
    ).exists()


@pytest.mark.django_db
def test_run_ygoprodeck_sync_skips_when_lock_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """If another run holds the advisory lock, this one skips: returns None, records no
    SyncRun, and writes nothing."""

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
