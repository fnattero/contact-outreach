from __future__ import annotations

import shutil
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.accounts.permissions import Capability, require_capability
from apps.configuration.integrations import (
    configured_integration_owner_id,
    get_gmail_oauth_client_secret,
    get_llm_api_key,
    runtime_integration_configuration,
)
from apps.configuration.models import SearchZone
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.models import GmailConnection
from apps.overture.services import get_active_snapshot


@require_GET
@never_cache
def liveness(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@require_GET
@never_cache
def readiness(request: HttpRequest) -> JsonResponse:
    components: dict[str, str] = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        components["database"] = "ok"
    except Exception:
        components["database"] = "unavailable"

    cache_key = f"health:{uuid4().hex}"
    try:
        cache.set(cache_key, "ok", timeout=5)
        components["redis"] = "ok" if cache.get(cache_key) == "ok" else "unavailable"
        cache.delete(cache_key)
    except Exception:
        components["redis"] = "unavailable"

    ready = all(value == "ok" for value in components.values())
    return JsonResponse(
        {"status": "ok" if ready else "unavailable"},
        status=200 if ready else 503,
    )


@require_capability(Capability.MANAGE_INTEGRATIONS)
@require_GET
@never_cache
def degraded(request: HttpRequest) -> JsonResponse:
    try:
        free_bytes = shutil.disk_usage(settings.PRIVATE_STORAGE_ROOT).free
        storage = "ok" if free_bytes >= settings.MIN_FREE_DISK_BYTES else "degraded"
    except OSError:
        free_bytes = 0
        storage = "unavailable"
    integration_owner_id: int | None = None
    try:
        integration_owner_id = configured_integration_owner_id()
        runtime = runtime_integration_configuration(integration_owner_id)
        gmail = GmailConnection.objects.order_by("-created_at").first()
        provider_supported = runtime.gmail_provider in {"api", "fake"}
        local_configuration_ready = runtime.gmail_provider == "fake" or bool(
            runtime.gmail_oauth_client_id
            and get_gmail_oauth_client_secret(integration_owner_id)
            and settings.FIELD_ENCRYPTION_KEY
        )
        if not provider_supported:
            gmail_status = "unsupported"
        elif not local_configuration_ready:
            gmail_status = "missing_configuration"
        elif gmail is None:
            gmail_status = "not_connected"
        elif gmail.is_ready and set(gmail.scopes) == set(GMAIL_SCOPES):
            gmail_status = "ready"
        else:
            gmail_status = "not_ready"
    except Exception:
        gmail_status = "unavailable"
        runtime = None
    try:
        if runtime is None or runtime.extractor_provider == "fake":
            extractor_status = "fake"
        else:
            snapshot = get_active_snapshot()
            if snapshot is None:
                extractor_status = "missing_dataset"
            else:
                active_hashes = set(
                    SearchZone.objects.filter(
                        active=True,
                        archived_at__isnull=True,
                    ).values_list("boundary_hash", flat=True)
                )
                covered_hashes = set(
                    snapshot.zones.filter(boundary_hash__in=active_hashes).values_list(
                        "boundary_hash", flat=True
                    )
                )
                extractor_status = (
                    "ready" if active_hashes and covered_hashes == active_hashes else "stale"
                )
    except Exception:
        extractor_status = "unavailable"
    try:
        llm_status = (
            "fake"
            if runtime is not None and runtime.llm_provider == "fake"
            else (
                "configured"
                if runtime is not None
                and (runtime.llm_provider == "ollama" or get_llm_api_key(integration_owner_id))
                else "missing_configuration"
            )
        )
    except Exception:
        llm_status = "unavailable"
    components = {
        "storage": storage,
        "gmail": gmail_status,
        "extractor": extractor_status,
        "llm": llm_status,
    }
    healthy_values = {"ok", "fake", "configured", "ready"}
    status = "ok" if all(value in healthy_values for value in components.values()) else "degraded"
    return JsonResponse(
        {"status": status, "components": components, "storage_free_bytes": free_bytes}
    )
