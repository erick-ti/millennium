from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.valuation.sync import run_valuation


class Command(BaseCommand):
    help = "Value every portfolio for today and write append-only PortfolioValueSnapshots."

    def handle(self, *args: Any, **options: Any) -> None:
        # Run the valuation engine under the advisory lock + the same-day-pricing
        # dependency, recording a ValuationRun: the same orchestration the Celery task
        # uses. Always values today: there is deliberately no --date option, because
        # holdings are taken as current, so a backdated run would write an unfixable
        # misdated row (PortfolioValueSnapshot is unique-per-day and append-only).
        result = run_valuation()
        if result is None:
            self.stdout.write(
                self.style.WARNING(
                    "Valuation skipped: another run is in progress, or no successful "
                    "TCGCSV pricing run exists for today (see the ValuationRun history)."
                )
            )
            return
        self.stdout.write(
            self.style.SUCCESS(
                "Valuation complete: "
                f"{result.portfolios_seen} portfolios, "
                f"{result.snapshots_created} snapshots written / "
                f"{result.snapshots_existing} already present; "
                f"holdings {result.holdings_valued} valued / "
                f"{result.holdings_unpriced} unpriced."
            )
        )
