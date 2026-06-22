from __future__ import annotations

from typing import Any

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandParser

from apps.core.permissions import DEMO_USERNAME


class Command(BaseCommand):
    """Seed the read-only demo account if it's missing — create-only by default.

    The recruiter showcase signs in via ``POST /api/auth/demo-login/``, which ``login()``s
    this account; ``DemoReadOnly`` then blocks every write for it. The seeded account is
    deliberately:

    * **unprivileged** — no ``is_staff`` / ``is_superuser``, so it can't reach ``/admin/``;
    * **password-less** — ``create_user`` with no password yields an unusable password, so
      it can be entered ONLY through the demo-login endpoint (no credential to leak, and the
      normal login form can never authenticate it).

    Runs on every deploy as part of the migrate one-shot (``infra/hetzner/docker-compose.yml``
    → the ``migrate`` service), so a clean automatic deploy ships the demo ready — the public
    CTA never lands on a missing account.

    **Create-only — an EXISTING account is left untouched** unless ``--repair`` is passed
    (Codex review 2026-06-21). Forcibly reactivating / de-privileging the row on every deploy
    would (a) defeat a deliberate ``is_active=False`` kill switch and (b) silently rewrite a
    real account that merely happens to be named ``demo``. The security boundary is
    ``DemoLoginView``, which re-checks the posture at request time and 404s a
    staff/superuser/passworded ``demo`` — so an un-repaired account reads as "demo
    unavailable", never as an exploitable session. ``--repair`` is the explicit operator
    opt-in to (re)claim an existing account for the demo posture.
    """

    help = "Seed the read-only demo account if missing (--repair to reclaim an existing one)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--email",
            default="",
            help="Optional email for a NEWLY created demo account (default: blank).",
        )
        parser.add_argument(
            "--repair",
            action="store_true",
            help=(
                "Force the safe posture (unprivileged, password-less, active) on an EXISTING "
                "account. Off by default so a deliberate disable or a real 'demo' account is "
                "never silently rewritten on deploy."
            ),
        )

    def _apply_demo_posture(self, user: User) -> None:
        user.is_staff = False
        user.is_superuser = False
        user.is_active = True
        user.set_unusable_password()
        user.save()

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            user = User.objects.get(username=DEMO_USERNAME)
        except User.DoesNotExist:
            # create_user with no password → unusable password; defaults are unprivileged +
            # active. That IS the demo posture, so no extra writes needed on the create path.
            User.objects.create_user(username=DEMO_USERNAME, email=options["email"])
            self.stdout.write(
                self.style.SUCCESS(f"Created read-only demo account '{DEMO_USERNAME}'.")
            )
            return

        in_posture = (
            user.is_active
            and not user.is_staff
            and not user.is_superuser
            and not user.has_usable_password()
        )
        if in_posture:
            self.stdout.write(f"Demo account '{DEMO_USERNAME}' already present.")
            return

        if not options["repair"]:
            # Left as-is on purpose: this is either a deliberate kill-switch disable or a
            # non-seed account. demo-login will treat it as unavailable (404) — safe, not a
            # hole. A WARNING (not an error) so the deploy one-shot still exits 0.
            self.stdout.write(
                self.style.WARNING(
                    f"A '{DEMO_USERNAME}' account exists but is not in the read-only demo "
                    "posture (disabled, privileged, or has a password) — leaving it "
                    "untouched; demo-login will treat it as unavailable. Re-run with "
                    "--repair to force the demo posture."
                )
            )
            return

        self._apply_demo_posture(user)
        self.stdout.write(
            self.style.SUCCESS(f"Repaired read-only demo account '{DEMO_USERNAME}'.")
        )
