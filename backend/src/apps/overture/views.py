from __future__ import annotations

from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import Capability, require_capability, workspace_for_user
from apps.audit.services import record_event
from apps.configuration.models import SearchZone
from apps.overture.models import (
    OvertureCoveragePartition,
    OvertureDatasetSnapshot,
    OvertureReleaseCheck,
)
from apps.overture.releases import validate_release_id
from apps.overture.services import get_active_snapshot, get_latest_snapshot
from apps.overture.tasks import sync_snapshot

_SYNC_POST_FIELDS = frozenset({"csrfmiddlewaretoken", "release_id", "province_code"})


def _release_catalog() -> tuple[OvertureReleaseCheck | None, str]:
    latest_check = OvertureReleaseCheck.objects.first()
    successful = OvertureReleaseCheck.objects.filter(
        status=OvertureReleaseCheck.Status.SUCCEEDED
    ).first()
    if successful is None:
        return None, (
            "Todavía no hay una comprobación exitosa del catálogo oficial. "
            "El worker de mantenimiento la ejecuta diariamente."
        )
    if latest_check is not None and latest_check.status == OvertureReleaseCheck.Status.FAILED:
        return successful, (
            "La comprobación diaria más reciente falló; se muestra la última metadata válida."
        )
    return successful, ""


@require_capability(Capability.MANAGE_INTEGRATIONS)
@require_GET
@never_cache
def overture_datasets(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.MANAGE_INTEGRATIONS)
    active_snapshot = get_active_snapshot()
    latest_snapshot = get_latest_snapshot()
    catalog, release_error = _release_catalog()

    coverage_zones: list[dict[str, object]] = []
    zone_source_counts: list[dict[str, object]] = []
    license_counts: list[dict[str, object]] = []
    if active_snapshot is not None:
        coverage_zones = list(
            active_snapshot.zones.order_by("name").values(
                "name",
                "boundary_hash",
                "source",
                "source_version",
                "attribution",
            )
        )
        zone_source_counts = [
            dict(row)
            for row in active_snapshot.zones.values("source", "source_version")
            .annotate(zone_count=Count("pk"))
            .order_by("source", "source_version")
        ]
        license_counts = list(
            active_snapshot.places.exclude(license="")
            .values("license")
            .annotate(place_count=Count("pk"))
            .order_by("-place_count", "license")[:100]
        )

    snapshots = list(OvertureDatasetSnapshot.objects.order_by("-created_at")[:25])
    partitions = list(
        OvertureCoveragePartition.objects.select_related(
            "release", "province", "snapshot"
        ).order_by("province_name", "-created_at")[:100]
    )
    provinces = list(
        SearchZone.objects.filter(
            workspace=workspace,
            level=SearchZone.Level.PROVINCE,
            active=True,
            archived_at__isnull=True,
        ).order_by("name")
    )
    return render(
        request,
        "overture/datasets.html",
        {
            "active_snapshot": active_snapshot,
            "latest_snapshot": latest_snapshot,
            "snapshots": snapshots,
            "release_catalog": catalog,
            "release_error": release_error,
            "coverage_zones": coverage_zones,
            "zone_source_counts": zone_source_counts,
            "license_counts": license_counts,
            "partitions": partitions,
            "provinces": provinces,
        },
    )


@require_capability(Capability.MANAGE_INTEGRATIONS)
@require_POST
@never_cache
def overture_sync(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.MANAGE_INTEGRATIONS)
    if set(request.POST).difference(_SYNC_POST_FIELDS):
        return HttpResponseBadRequest("La solicitud de sincronización es inválida.")

    submitted_releases = request.POST.getlist("release_id")
    submitted_provinces = request.POST.getlist("province_code")
    if len(submitted_releases) != 1 or len(submitted_provinces) != 1:
        return HttpResponseBadRequest("La solicitud de sincronización es inválida.")
    try:
        release_id = validate_release_id(submitted_releases[0])
    except ValidationError:
        return HttpResponseBadRequest("La solicitud de sincronización es inválida.")
    province = SearchZone.objects.filter(
        workspace=workspace,
        level=SearchZone.Level.PROVINCE,
        official_code=submitted_provinces[0],
        active=True,
        archived_at__isnull=True,
    ).first()
    if province is None:
        return HttpResponseBadRequest("La provincia elegida no está disponible.")

    catalog = OvertureReleaseCheck.objects.filter(
        status=OvertureReleaseCheck.Status.SUCCEEDED
    ).first()
    if catalog is None or not isinstance(catalog.releases, list):
        return HttpResponse(
            "Todavía no hay metadata oficial de Overture verificada por maintenance.",
            status=503,
        )
    if release_id not in catalog.releases or release_id != catalog.latest_release:
        return HttpResponseBadRequest("La solicitud de sincronización es inválida.")

    try:
        sync_snapshot.delay(release_id, province.official_code)
    except Exception:
        return HttpResponse(
            "No se pudo encolar la sincronización de Overture.",
            status=503,
        )

    record_event(
        action="overture.sync_queued",
        entity=SimpleNamespace(pk=release_id),
        entity_type="OvertureRelease",
        actor=owner,
        after={
            "release_id": release_id,
            "province_code": province.official_code,
            "queue": "maintenance",
        },
    )
    messages.success(
        request,
        f"La actualización de {province.name} quedó en cola. "
        "Podés seguir usando las otras provincias.",
    )
    return redirect("overture-datasets")
