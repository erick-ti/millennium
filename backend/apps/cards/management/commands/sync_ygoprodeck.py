from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.cards.sync import run_ygoprodeck_sync


class Command(BaseCommand):
    help = "Sync card and printing metadata from YGOPRODeck's bulk card dump."

    def handle(self, *args: Any, **options: Any) -> None:
        # Runs under the compare-to-previous cardinality guard and records a SyncRun
        # (DECISIONS 2026-05-24 slice 3) — same orchestration the Celery task uses.
        result = run_ygoprodeck_sync()
        if result is None:
            # Another run held the advisory lock — this invocation was skipped.
            self.stdout.write(
                self.style.WARNING("YGOPRODeck sync skipped: another run is already in progress.")
            )
            return
        self.stdout.write(
            self.style.SUCCESS(
                "YGOPRODeck sync complete: "
                f"cards {result.cards_created} new / {result.cards_updated} updated / "
                f"{result.cards_unchanged} unchanged, "
                f"printings {result.printings_created} new / {result.printings_updated} updated / "
                f"{result.printings_unchanged} unchanged"
            )
        )
