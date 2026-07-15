from __future__ import annotations

from uuid import uuid4

from django.core.cache import cache
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


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
