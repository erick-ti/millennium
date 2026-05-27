from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.imports.dragon_shield import ImportParseError
from apps.imports.models import ImportStatus
from apps.imports.sync import preview_import, run_import


class Command(BaseCommand):
    help = "Import a Dragon Shield CSV export: parse, match printings, and materialize EXACT matches."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("path", type=str, help="Path to the Dragon Shield CSV export.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse, normalize, and match only — preview the outcome, write nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["path"])
        try:
            # Decode as utf-8-sig so a leading BOM (Excel "CSV UTF-8" saves add one) is
            # stripped at decode; parse_dragon_shield strips one defensively too. Slice 4
            # owns the decode (the slice-2 obligation).
            content = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise CommandError(f"cannot read {path}: {exc}") from exc

        if options["dry_run"]:
            self._dry_run(content)
            return

        result = run_import(content, filename=path.name)
        if result.status == ImportStatus.FAILED.value:
            raise CommandError(
                f"import failed: {path.name} is not a Dragon Shield export "
                f"(recorded as batch {result.batch_id})."
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Import batch {result.batch_id} ({result.status}): {result.rows_total} rows -- "
                f"{result.rows_materialized} materialized / "
                f"{result.rows_skipped_duplicate} skipped (duplicate) / "
                f"{result.rows_pending_review} pending review / "
                f"{result.rows_error} error."
            )
        )
        if not result.materialization_allowed:
            self.stdout.write(
                self.style.WARNING(
                    "No successful TCGCSV reconciliation today, so EXACT matches were staged "
                    "for review, not materialized. Run sync_tcgcsv, then re-import or approve."
                )
            )

    def _dry_run(self, content: str) -> None:
        try:
            preview = preview_import(content)
        except ImportParseError as exc:
            raise CommandError(f"not a Dragon Shield export: {exc}") from exc
        self.stdout.write(self.style.WARNING("DRY RUN -- no batch, rows, or holdings written."))
        self.stdout.write(
            f"{preview.rows_total} rows: {preview.exact} EXACT / {preview.medium} MEDIUM / "
            f"{preview.unmatched} unmatched / {preview.rows_with_issues} with issues. "
            f"TCGCSV reconciliation fresh today: "
            f"{'yes' if preview.reconciliation_fresh else 'no'}."
        )
        if preview.exact and not preview.reconciliation_fresh:
            self.stdout.write(
                self.style.WARNING(
                    f"{preview.exact} EXACT rows would be STAGED (not materialized): no "
                    "successful TCGCSV reconciliation today. Run sync_tcgcsv first."
                )
            )
