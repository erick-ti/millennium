from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.alerts.sync import run_alerts


class Command(BaseCommand):
    help = "Evaluate active price-alert rules against today's movers and record AlertEvents."

    def handle(self, *args: Any, **options: Any) -> None:
        # Run the evaluation under the advisory lock + the same-day-pricing dependency,
        # recording an AlertRun (Phase 5 slice 4) -- the same orchestration the Celery
        # task uses. Always evaluates today (no --date): the movers anchors are
        # today / today-window, and events are keyed on today's UTC day.
        result = run_alerts()
        if result is None:
            self.stdout.write(
                self.style.WARNING(
                    "Alerts skipped: another run is in progress, or no successful "
                    "TCGCSV pricing run exists for today (see the AlertRun history)."
                )
            )
            return
        self.stdout.write(
            self.style.SUCCESS(
                "Alerts complete: "
                f"{result.rules_evaluated} rules evaluated, "
                f"{result.events_created} events created "
                f"({result.events_existing} already present)."
            )
        )
