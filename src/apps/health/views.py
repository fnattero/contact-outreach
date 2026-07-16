from __future__ import annotations

import shutil
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.models import GmailConnection


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
        {"status": "ok" if ready else "unavailable", "components": components},
        status=200 if ready else 503,
    )


@require_GET
@never_cache
def degraded(request: HttpRequest) -> JsonResponse:
    try:
        free_bytes = shutil.disk_usage(settings.PRIVATE_STORAGE_ROOT).free
        storage = "ok" if free_bytes >= settings.MIN_FREE_DISK_BYTES else "degraded"
    except OSError:
        free_bytes = 0
        storage = "unavailable"
    try:
        gmail = GmailConnection.objects.order_by("-created_at").first()
        provider_supported = settings.GMAIL_PROVIDER in {"api", "fake"}
        local_configuration_ready = settings.GMAIL_PROVIDER == "fake" or bool(
            settings.GMAIL_OAUTH_CLIENT_ID
            and settings.GMAIL_OAUTH_CLIENT_SECRET
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
    components = {
        "storage": storage,
        "gmail": gmail_status,
        "extractor": "fake"
        if settings.EXTRACTOR_PROVIDER == "fake"
        else ("configured" if settings.OUTSCRAPER_API_KEY else "missing_configuration"),
        "llm": "fake"
        if settings.LLM_PROVIDER == "fake"
        else (
            "configured"
            if settings.LLM_PROVIDER == "ollama" or settings.LLM_API_KEY
            else "missing_configuration"
        ),
    }
    healthy_values = {"ok", "fake", "configured", "ready"}
    status = "ok" if all(value in healthy_values for value in components.values()) else "degraded"
    return JsonResponse(
        {"status": status, "components": components, "storage_free_bytes": free_bytes}
    )
