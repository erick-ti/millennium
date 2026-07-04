"""Seed the minimal fixtures the Playwright smoke suite needs (Phase 5 slice 6).

No app fixtures exist, so the end-to-end smoke flows need a deterministic seed:

* a login user (every page 403s without a session);
* an **import-target** Card + CardPrinting whose name/set_code/set_rarity align
  with the smoke Dragon Shield CSV, so the upload matches EXACT, but with NO
  same-day TCGCSV reconciliation `SyncRun`, an EXACT row stages PENDING (it is
  NOT auto-materialized), so it lands in the review queue for the smoke to
  Approve (a human approve overrides the freshness gate). This card is NOT
  pre-owned: the import flow is what materializes it into the collection.
* a **pre-owned** Card + CardPrinting + CollectionItem + CollectionLot
  (quantity > 0, because the deck zero-copy guard rejects lot-less holdings),
  the holding the deck flow tags into a new deck.

Idempotent: every object is found-or-created, so re-running is safe. `--reset`
first removes the smoke-owned rows (identified by the ``SMOKE-`` set_code
prefix, the smoke card names, the smoke portfolio names, the smoke CSV
filename, and the ``Smoke E2E`` deck-name prefix the deck spec uses) so a local
re-run starts clean, in particular it drops the import-created holding so the
next import materializes afresh. A CI run starts from an empty DB, where
``--reset`` is a no-op. Scoped to smoke-marked data only, so it is safe to run
against a shared dev database. That contract covers the login user too: the
seed claims the ``smoke`` account only when it looks seed-owned (the smoke
email, no staff/superuser bits) and fails closed on a colliding real account
rather than resetting its password to the committed value.

Two fail-closed guards, on different axes: ``DEBUG=False`` refuses (prod
*settings posture*; ``--force`` bypasses, that's how the test settings run it),
and a non-loopback *database target* refuses regardless of ``--force``
(``SEED_SMOKE_ALLOW_REMOTE_DB=1`` is the only override). The smoke suite is
local/CI by definition; a mis-pointed ``DATABASE_URL`` must not receive a
known-password account.

The import smoke relies on the import-target row staging PENDING (no same-day
successful TCGCSV reconciliation covers a printing created after it). ``--reset``
re-creates that printing fresh, so any earlier same-day reconciliation predates
it and the row stays PENDING. The one way to break this is to run ``sync_tcgcsv``
AFTER the seed against the same DB before the smoke runs, don't.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from apps.cards.models import Card, CardPrinting
from apps.collection.models import CollectionItem, CollectionLot, Condition, Language
from apps.core.enums import Edition
from apps.decks.models import Deck
from apps.imports.models import ImportBatch
from apps.portfolio.models import Portfolio

# Login credentials the smoke specs use. A throwaway local/CI seed account, never
# created on a prod-posture or remote target (the two guards below refuse), not
# a real secret.
SMOKE_USERNAME = "smoke"
SMOKE_PASSWORD = "smoke-password"
SMOKE_EMAIL = "smoke@example.invalid"

# DB hosts the seed treats as local. "" = a unix-socket connection.
_LOOPBACK_DB_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})

# Import-target card: the smoke CSV references it; matches EXACT, stages PENDING.
IMPORT_CARD_NAME = "Smoke Import Dragon"
IMPORT_SET_CODE = "SMOKE-EN001"
IMPORT_SET_RARITY = "Ultra Rare"  # the mapped name for DS code "UR"

# Pre-owned card: the holding the deck flow tags into a deck.
DECK_CARD_NAME = "Smoke Deck Token"
DECK_SET_CODE = "SMOKE-EN002"
DECK_SET_RARITY = "Secret Rare"

SMOKE_SET_NAME = "Smoke Set"
SMOKE_SET_CODE_PREFIX = "SMOKE-"
SMOKE_PORTFOLIO_NAME = "Smoke Portfolio"
# The DS CSV's "Folder Name" → a Portfolio find-or-created on approve.
SMOKE_IMPORT_FOLDER = "Smoke Imports"
# The uploaded fixture's filename (frontend/e2e/fixtures/smoke-collection.csv).
SMOKE_CSV_FILENAME = "smoke-collection.csv"
# The deck spec names its decks with this prefix (+ a unique suffix per run).
SMOKE_DECK_NAME_PREFIX = "Smoke E2E"


def _database_target_is_local() -> bool:
    """True when the default DB is sqlite (always a local file/memory), a
    unix-socket connection (empty HOST), or a loopback host. This covers every
    legitimate smoke target (the compose Postgres on 127.0.0.1, the CI service
    on localhost, and the sqlite test settings) while a deployed/remote host
    fails it."""
    db = settings.DATABASES["default"]
    if "sqlite" in str(db.get("ENGINE", "")):
        return True
    return str(db.get("HOST", "")) in _LOOPBACK_DB_HOSTS


class Command(BaseCommand):
    help = "Seed the minimal fixtures the Playwright end-to-end smoke suite needs."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the smoke-owned rows before seeding (clean slate for a local re-run).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even when DEBUG is False. Refused otherwise to avoid seeding a prod-like DB.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Two fail-closed guards on two different axes:
        # DEBUG guards the *settings posture* of the operator's shell; the host
        # check guards the *database actually being mutated*. DEBUG alone is not
        # a boundary, since config.settings.smoke inherits dev (DEBUG=True always),
        # so a DATABASE_URL mis-pointed at a deployed DB would sail through it
        # and seed a known-password account into an app whose entire API is
        # plain IsAuthenticated (no per-user scoping). The smoke suite is
        # local/CI by definition, so a non-loopback DB target is refused, and
        # deliberately NOT bypassed by --force, whose only meaning is "skip the
        # DEBUG check" (the test settings run with DEBUG=False); a remote seed
        # is a separate, more dangerous decision that needs its own explicit act.
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "seed_smoke refuses to run with DEBUG=False (this looks like production). "
                "Run it under config.settings.smoke, or pass --force if you really mean to."
            )
        if not _database_target_is_local() and os.environ.get("SEED_SMOKE_ALLOW_REMOTE_DB") != "1":
            host = settings.DATABASES["default"].get("HOST", "")
            raise CommandError(
                f"seed_smoke refuses to touch a non-local database (host {host!r}): "
                "it writes a known-password login account. Point DATABASE_URL at a "
                "local Postgres, or set SEED_SMOKE_ALLOW_REMOTE_DB=1 if you really "
                "mean to seed a remote database (--force does NOT bypass this)."
            )

        with transaction.atomic():
            if options["reset"]:
                self._reset()
            self._seed()

    def _reset(self) -> None:
        """Remove smoke-marked rows, children-first so PROTECT FKs don't block.

        Deleting a CollectionItem cascades its lots and any deck memberships
        (both CASCADE), so the import-created holding for the import-target card
        is cleared and the next import materializes afresh.

        Smoke portfolios are deleted ONLY when they have no valuation history:
        on a shared dev DB the beat-scheduled valuation values EVERY portfolio,
        and ``PortfolioValueSnapshot.portfolio`` is PROTECT (append-only audit
        history this command must not destroy), so an unconditional delete would
        raise ``ProtectedError`` and roll back the whole reset.
        A surviving empty portfolio is harmless: the seed and the import flow
        both find-or-create by name, so it is simply reused.
        """
        Deck.objects.filter(name__startswith=SMOKE_DECK_NAME_PREFIX).delete()
        ImportBatch.objects.filter(original_filename=SMOKE_CSV_FILENAME).delete()
        CollectionItem.objects.filter(
            printing__set_code__startswith=SMOKE_SET_CODE_PREFIX
        ).delete()
        CardPrinting.objects.filter(set_code__startswith=SMOKE_SET_CODE_PREFIX).delete()
        Card.objects.filter(name__in=[IMPORT_CARD_NAME, DECK_CARD_NAME]).delete()
        smoke_portfolios = Portfolio.objects.filter(
            name__in=[SMOKE_PORTFOLIO_NAME, SMOKE_IMPORT_FOLDER]
        )
        kept = smoke_portfolios.filter(value_snapshots__isnull=False).distinct().count()
        smoke_portfolios.filter(value_snapshots__isnull=True).delete()
        if kept:
            self.stdout.write(
                f"Reset: kept {kept} smoke portfolio(s) with PROTECTed valuation "
                "history (emptied and reused instead of deleted)."
            )
        self.stdout.write("Reset: smoke-owned rows removed.")

    def _seed(self) -> None:
        user, created = User.objects.get_or_create(
            username=SMOKE_USERNAME,
            defaults={"email": SMOKE_EMAIL, "is_active": True},
        )
        # Ownership check: the seed may only (re)set credentials
        # on the account IT created, marked by the smoke email and no privilege
        # bits. A pre-existing account that merely shares the username must not
        # be silently converted into a known-password login (the "smoke-marked
        # data only" contract applies to the user row too). Fail closed; the
        # atomic transaction in handle() rolls back the get_or_create.
        if not created and (user.email != SMOKE_EMAIL or user.is_staff or user.is_superuser):
            raise CommandError(
                f"A user named '{SMOKE_USERNAME}' already exists and does not look "
                "seed-owned (different email, or staff/superuser). Refusing to reset "
                "its credentials, rename or remove that account first."
            )
        # (Re)set the credentials on the seed-owned row so login is deterministic
        # across runs; keep it unprivileged (the smoke flows use the regular API,
        # never the admin).
        user.is_active = True
        user.email = SMOKE_EMAIL
        user.is_staff = False
        user.is_superuser = False
        user.set_password(SMOKE_PASSWORD)
        user.save()

        # Import-target printing: matched EXACT by the smoke CSV, staged PENDING
        # because the seed records NO same-day TCGCSV reconciliation SyncRun. Not
        # pre-owned: the import flow materializes it.
        import_card, _ = Card.objects.get_or_create(name=IMPORT_CARD_NAME)
        CardPrinting.objects.get_or_create(
            card=import_card,
            set_code=IMPORT_SET_CODE,
            set_rarity=IMPORT_SET_RARITY,
            variant_label=None,
            defaults={"set_name": SMOKE_SET_NAME, "is_multi_variant": False},
        )

        # Pre-owned holding for the deck flow.
        deck_card, _ = Card.objects.get_or_create(name=DECK_CARD_NAME)
        deck_printing, _ = CardPrinting.objects.get_or_create(
            card=deck_card,
            set_code=DECK_SET_CODE,
            set_rarity=DECK_SET_RARITY,
            variant_label=None,
            defaults={"set_name": SMOKE_SET_NAME, "is_multi_variant": False},
        )
        portfolio, _ = Portfolio.objects.get_or_create(name=SMOKE_PORTFOLIO_NAME)
        item, _ = CollectionItem.objects.get_or_create(
            printing=deck_printing,
            portfolio=portfolio,
            condition=Condition.NEAR_MINT.value,
            edition=Edition.FIRST_EDITION.value,
            language=Language.ENGLISH.value,
        )
        # quantity > 0 is required: the deck zero-copy guard rejects a lot-less
        # holding (quantity 0) → 400.
        CollectionLot.objects.get_or_create(
            collection_item=item,
            import_source_ref=None,
            defaults={"quantity": 2, "unit_cost": Decimal("1.00"), "acquired_at": date(2024, 1, 1)},
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded smoke fixtures: user '{SMOKE_USERNAME}', "
                f"import target '{IMPORT_CARD_NAME}' ({IMPORT_SET_CODE}), "
                f"owned holding '{DECK_CARD_NAME}' ({DECK_SET_CODE}, qty 2)."
            )
        )
