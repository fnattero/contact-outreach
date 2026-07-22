from __future__ import annotations

import shutil
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.catalogs.models import Catalog
from apps.catalogs.services import verify_catalog
from apps.configuration.integrations import validate_encrypted_integration_credentials
from apps.mailbox.crypto import decrypt_token
from apps.mailbox.models import GmailConnection


class Command(BaseCommand):
    help = "Verify migrations, private catalog integrity, token decryptability and disk headroom."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--require-kill-switch",
            action="store_true",
            help="Fail if live is effective while the kill switch is disabled.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        failures: list[str] = []
        executor = MigrationExecutor(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            failures.append("hay migraciones sin aplicar")
        for catalog in Catalog.objects.all():
            try:
                verify_catalog(catalog)
            except Exception:
                failures.append(f"catálogo {catalog.pk} ausente o con hash inválido")
        for gmail in GmailConnection.objects.exclude(refresh_token_encrypted=""):
            try:
                decrypt_token(gmail.refresh_token_encrypted)
            except Exception:
                failures.append(f"token Gmail {gmail.pk} no descifrable")
        failures.extend(validate_encrypted_integration_credentials())
        try:
            free = shutil.disk_usage(settings.PRIVATE_STORAGE_ROOT).free
        except OSError:
            failures.append("almacenamiento privado no disponible")
        else:
            if free < settings.MIN_FREE_DISK_BYTES:
                failures.append("espacio libre debajo del margen operativo")
        if (
            options["require_kill_switch"]
            and settings.SEND_MODE == "live"
            and not settings.SEND_KILL_SWITCH
        ):
            failures.append("restore ejecutado con live habilitado y kill switch inactivo")
        if failures:
            raise CommandError("Restore no verificable: " + "; ".join(failures))
        self.stdout.write(self.style.SUCCESS("Restore verificado; live permanece bajo control."))
