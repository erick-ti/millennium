"""Tests for the `seed_smoke` management command (Phase 5 slice 6).

The command is test infrastructure (it primes the Playwright smoke DB), but it
touches enough models that a field/constraint change could silently break it,
so these tests guard the contract the smoke suite relies on: a login user, an
import-target printing that is NOT yet owned, and a pre-owned holding with a
positive quantity. Runs under `config.settings.test` (DEBUG=False), so it
passes `--force` to bypass the production guard.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.cards.models import Card, CardPrinting
from apps.collection.models import CollectionItem, CollectionLot
from apps.core.management.commands.seed_smoke import (
    DECK_CARD_NAME,
    DECK_SET_CODE,
    IMPORT_CARD_NAME,
    IMPORT_SET_CODE,
    IMPORT_SET_RARITY,
    SMOKE_CSV_FILENAME,
    SMOKE_IMPORT_FOLDER,
    SMOKE_PASSWORD,
    SMOKE_PORTFOLIO_NAME,
    SMOKE_USERNAME,
    _database_target_is_local,
)
from apps.imports.dragon_shield import normalize_row, parse_dragon_shield
from apps.imports.matching import match_row
from apps.imports.models import ImportRow, MatchConfidence, RowStatus
from apps.imports.sync import approve_row, run_import
from apps.portfolio.models import Portfolio, PortfolioValueSnapshot

# The committed Dragon Shield fixture the Playwright import smoke uploads, resolved
# from the repo root (backend/tests/ → parents[2]).
_SMOKE_CSV_PATH = (
    Path(__file__).resolve().parents[2] / "frontend" / "e2e" / "fixtures" / SMOKE_CSV_FILENAME
)


@pytest.mark.django_db
def test_seed_creates_login_user_with_working_password() -> None:
    call_command("seed_smoke", "--force")

    user = User.objects.get(username=SMOKE_USERNAME)
    assert user.is_active is True
    # Unprivileged by construction: the smoke flows use the regular API.
    assert user.is_staff is False
    assert user.is_superuser is False
    # The password must actually authenticate (set_password, not a raw assign).
    assert authenticate(username=SMOKE_USERNAME, password=SMOKE_PASSWORD) == user


@pytest.mark.django_db
def test_seed_import_target_is_matchable_but_not_yet_owned() -> None:
    call_command("seed_smoke", "--force")

    printing = CardPrinting.objects.get(set_code=IMPORT_SET_CODE)
    assert printing.card.name == IMPORT_CARD_NAME
    assert printing.variant_label is None
    assert printing.is_multi_variant is False
    # The import flow is what materializes it, the seed must not pre-own it,
    # else the smoke's approve would SKIP ("already imported") instead of add.
    assert not CollectionItem.objects.filter(printing=printing).exists()


@pytest.mark.django_db
def test_seed_owned_holding_has_positive_quantity() -> None:
    call_command("seed_smoke", "--force")

    printing = CardPrinting.objects.get(set_code=DECK_SET_CODE)
    assert printing.card.name == DECK_CARD_NAME
    item = CollectionItem.objects.get(printing=printing)
    # quantity = SUM of lots; the deck zero-copy guard requires > 0.
    total = sum(lot.quantity for lot in item.lots.all())
    assert total == 2


@pytest.mark.django_db
def test_seed_is_idempotent() -> None:
    call_command("seed_smoke", "--force")
    counts = (
        User.objects.count(),
        Card.objects.count(),
        CardPrinting.objects.count(),
        CollectionItem.objects.count(),
        CollectionLot.objects.count(),
        Portfolio.objects.count(),
    )

    call_command("seed_smoke", "--force")

    assert counts == (
        User.objects.count(),
        Card.objects.count(),
        CardPrinting.objects.count(),
        CollectionItem.objects.count(),
        CollectionLot.objects.count(),
        Portfolio.objects.count(),
    )


@pytest.mark.django_db
def test_reset_removes_smoke_rows() -> None:
    call_command("seed_smoke", "--force")
    assert CardPrinting.objects.filter(set_code=DECK_SET_CODE).exists()

    call_command("seed_smoke", "--reset", "--force")

    # Re-seeded (so the suite can run again), and no duplication: exactly one of
    # each smoke printing remains after reset+seed.
    assert CardPrinting.objects.filter(set_code=IMPORT_SET_CODE).count() == 1
    assert CardPrinting.objects.filter(set_code=DECK_SET_CODE).count() == 1
    assert CollectionItem.objects.filter(printing__set_code=DECK_SET_CODE).count() == 1


def test_refuses_to_run_when_not_debug_without_force(monkeypatch: pytest.MonkeyPatch) -> None:
    # The guard reads settings.DEBUG; patch it False to simulate prod. It fires
    # before any DB access, so no django_db marker is needed.
    monkeypatch.setattr(django_settings, "DEBUG", False)
    with pytest.raises(CommandError, match="production"):
        call_command("seed_smoke")


def test_refuses_a_remote_database_target_even_with_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEBUG guards the settings posture, not the mutation target: smoke/dev
    settings are always DEBUG=True, so a DATABASE_URL mis-pointed at a
    deployed DB would pass that check alone. The host guard refuses a
    non-loopback target, and --force must NOT bypass it (its only meaning is
    "skip the DEBUG check"). Raises before any DB access → no django_db marker.
    """
    monkeypatch.setitem(
        django_settings.DATABASES["default"], "ENGINE", "django.db.backends.postgresql"
    )
    monkeypatch.setitem(django_settings.DATABASES["default"], "HOST", "db.prod.example.com")

    with pytest.raises(CommandError, match="non-local database"):
        call_command("seed_smoke", "--force")


@pytest.mark.django_db
def test_remote_database_guard_has_an_explicit_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEED_SMOKE_ALLOW_REMOTE_DB=1 is the one deliberate escape hatch. The
    guard reads settings only; the live test connection (sqlite) is untouched,
    so the command then completes against it.
    """
    monkeypatch.setitem(
        django_settings.DATABASES["default"], "ENGINE", "django.db.backends.postgresql"
    )
    monkeypatch.setitem(django_settings.DATABASES["default"], "HOST", "db.prod.example.com")
    monkeypatch.setenv("SEED_SMOKE_ALLOW_REMOTE_DB", "1")

    call_command("seed_smoke", "--force")  # must not raise

    assert User.objects.filter(username=SMOKE_USERNAME).exists()


def test_database_locality_predicate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin what counts as local: sqlite always; postgres on a unix socket
    (empty HOST) or loopback; anything else is remote."""
    db = django_settings.DATABASES["default"]

    monkeypatch.setitem(db, "ENGINE", "django.db.backends.sqlite3")
    monkeypatch.setitem(db, "HOST", "db.prod.example.com")
    assert _database_target_is_local() is True  # sqlite is local regardless of HOST

    monkeypatch.setitem(db, "ENGINE", "django.db.backends.postgresql")
    for host in ("", "localhost", "127.0.0.1", "::1"):
        monkeypatch.setitem(db, "HOST", host)
        assert _database_target_is_local() is True, host

    for host in ("db.prod.example.com", "10.0.0.5", "millennium.internal"):
        monkeypatch.setitem(db, "HOST", host)
        assert _database_target_is_local() is False, host


@pytest.mark.django_db
def test_refuses_to_claim_a_preexisting_non_seed_smoke_user() -> None:
    """A real account that merely shares the smoke username must NOT be turned
    into a known-password login: the seed owns only the user it
    created (smoke email, no privilege bits) and fails closed on anything else,
    leaving the account's credentials and privileges untouched.
    """
    real = User.objects.create_user(
        username=SMOKE_USERNAME, email="owner@example.com", password="real-password"
    )
    real.is_staff = True
    real.is_superuser = True
    real.save()

    with pytest.raises(CommandError, match="does not look seed-owned"):
        call_command("seed_smoke", "--force")

    real.refresh_from_db()
    assert real.is_staff is True
    assert real.is_superuser is True
    assert authenticate(username=SMOKE_USERNAME, password="real-password") == real
    assert authenticate(username=SMOKE_USERNAME, password=SMOKE_PASSWORD) is None


@pytest.mark.django_db
def test_smoke_csv_fixture_resolves_exact_against_the_seeded_printing() -> None:
    """The committed e2e CSV, the seed constants, and the matcher are coupled by
    string value across files (CSV, seed_smoke.py, the DS mapping tables). This
    runs the REAL parser/matcher over the committed fixture against the seeded
    catalog, so a drift that would only red the advisory e2e job fails the
    required pytest gate instead. Imports the seed constants so the assertions
    can't drift from the seed.
    """
    call_command("seed_smoke", "--force")

    rows = parse_dragon_shield(_SMOKE_CSV_PATH.read_text(encoding="utf-8-sig"))
    assert len(rows) == 1

    norm = normalize_row(rows[0].raw)
    # No normalization issue → the row reaches the matcher and stages PENDING
    # (approvable), never ERROR.
    assert norm.issues == ()
    assert norm.data["card_name"] == IMPORT_CARD_NAME
    assert norm.data["set_code"] == IMPORT_SET_CODE
    assert norm.data["set_rarity"] == IMPORT_SET_RARITY

    # The seeded printing is matched EXACT (name agrees, not multi-variant): the
    # precondition for the import smoke's Approve step.
    result = match_row(norm.data)
    assert result.confidence == MatchConfidence.EXACT
    assert result.printing is not None
    assert result.printing.set_code == IMPORT_SET_CODE


@pytest.mark.django_db
def test_reset_removes_the_import_materialized_holding_and_folder() -> None:
    """Lock the docstring's "drops the import-created holding so the next import
    materializes afresh" contract: a real import + approve materializes a holding
    under the "Smoke Imports" folder portfolio, and --reset must remove both so a
    local re-run isn't a no-op SKIP.
    """
    call_command("seed_smoke", "--force")

    # No same-day TCGCSV reconciliation is seeded → the EXACT row stages PENDING;
    # a human approve overrides the freshness gate and materializes the holding.
    result = run_import(_SMOKE_CSV_PATH.read_text(encoding="utf-8-sig"), filename=SMOKE_CSV_FILENAME)
    row = ImportRow.objects.get(batch_id=result.batch_id)
    assert row.status == RowStatus.PENDING
    approve_row(row)

    assert CollectionItem.objects.filter(printing__set_code=IMPORT_SET_CODE).exists()
    assert Portfolio.objects.filter(name=SMOKE_IMPORT_FOLDER).exists()

    call_command("seed_smoke", "--reset", "--force")

    assert not CollectionItem.objects.filter(printing__set_code=IMPORT_SET_CODE).exists()
    assert not Portfolio.objects.filter(name=SMOKE_IMPORT_FOLDER).exists()


@pytest.mark.django_db
def test_reset_survives_protected_valuation_history_on_a_smoke_portfolio() -> None:
    """On a shared dev DB the beat-scheduled valuation values EVERY portfolio,
    including the smoke ones, and ``PortfolioValueSnapshot.portfolio`` is
    PROTECT. ``--reset`` must skip (not delete) a smoke portfolio with that
    history: an unconditional delete raises ``ProtectedError`` and rolls back
    the whole reset, leaving the smoke suite unrunnable.
    """
    call_command("seed_smoke", "--force")
    portfolio = Portfolio.objects.get(name=SMOKE_PORTFOLIO_NAME)
    # Full coverage, so the gain_iff_complete CHECK requires gain = market - cost.
    snapshot = PortfolioValueSnapshot.objects.create(
        portfolio=portfolio,
        snapshot_date=timezone.localdate(),
        market_value=Decimal("10.00"),
        liquidation_value=Decimal("8.50"),
        cost_basis=Decimal("4.00"),
        unrealized_gain=Decimal("6.00"),
        total_card_count=2,
        priced_card_count=2,
        costed_card_count=2,
        valuation_method="test",
        valuation_version=1,
    )

    # Must not raise ProtectedError.
    call_command("seed_smoke", "--reset", "--force")

    # The append-only history and its portfolio survive (emptied + reused) ...
    assert PortfolioValueSnapshot.objects.filter(pk=snapshot.pk).exists()
    assert Portfolio.objects.filter(pk=portfolio.pk).count() == 1
    # ... and the rest of the reset + re-seed still happened, without duplication.
    assert CardPrinting.objects.filter(set_code=DECK_SET_CODE).count() == 1
    assert CollectionItem.objects.filter(printing__set_code=DECK_SET_CODE).count() == 1
