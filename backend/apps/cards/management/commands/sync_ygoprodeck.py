from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.cards.sync import sync_cards_from_metadata
from apps.pricing.providers.ygoprodeck import YgoprodeckProvider


class Command(BaseCommand):
    help = "Sync card and printing metadata from YGOPRODeck's bulk card dump."

    def handle(self, *args: Any, **options: Any) -> None:
        result = sync_cards_from_metadata(YgoprodeckProvider())
        self.stdout.write(
            self.style.SUCCESS(
                "YGOPRODeck sync complete: "
                f"cards {result.cards_created} new / {result.cards_updated} updated / "
                f"{result.cards_unchanged} unchanged, "
                f"printings {result.printings_created} new / {result.printings_updated} updated / "
                f"{result.printings_unchanged} unchanged"
            )
        )
