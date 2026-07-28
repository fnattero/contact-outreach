from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts.services import unlock_login


class Command(BaseCommand):
    help = "Remove fixed login lockouts without exposing stored HMAC identifiers."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--username", default="", help="Unlock every address for this user.")
        parser.add_argument("--ip", default="", help="Unlock records for this client IP.")

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        username = str(options["username"]).strip()
        client_ip = str(options["ip"]).strip()
        try:
            count = unlock_login(username=username, client_ip=client_ip)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
        self.stdout.write(self.style.SUCCESS(f"Login lockout records cleared: {count}."))
