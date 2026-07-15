from __future__ import annotations

import os
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts.services import OwnerConflictError, ensure_owner


class Command(BaseCommand):
    help = "Create or rotate the single owner from OWNER_* environment variables."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--if-configured",
            action="store_true",
            help="Exit successfully when OWNER_USERNAME or OWNER_PASSWORD are missing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        username = os.getenv("OWNER_USERNAME", "").strip()
        password = os.getenv("OWNER_PASSWORD", "")
        email = os.getenv("OWNER_EMAIL", "").strip()
        if not username or not password:
            if options["if_configured"]:
                self.stdout.write(
                    "Owner bootstrap skipped: OWNER_* credentials are not configured."
                )
                return
            raise CommandError("OWNER_USERNAME and OWNER_PASSWORD must be configured")
        try:
            result = ensure_owner(username=username, password=password, email=email)
        except OwnerConflictError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Owner bootstrap {result.action}."))
