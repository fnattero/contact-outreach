from __future__ import annotations

from celery import shared_task

from apps.overture.models import OvertureReleaseCheck
from apps.overture.releases import OFFICIAL_STAC_URL, discover_releases
from apps.overture.services import purge_retired_snapshots, sync_catalog


@shared_task(
    name="overture.discover_releases",
    queue="maintenance",
    soft_time_limit=45,
    time_limit=60,
)  # type: ignore[untyped-decorator]
def discover_releases_task() -> dict[str, object]:
    try:
        catalog = discover_releases()
    except Exception as exc:
        OvertureReleaseCheck.objects.create(
            status=OvertureReleaseCheck.Status.FAILED,
            catalog_url=OFFICIAL_STAC_URL,
            error=f"{type(exc).__name__}: no se pudo consultar el catálogo oficial.",
        )
        return {"status": "FAILED"}
    OvertureReleaseCheck.objects.create(
        status=OvertureReleaseCheck.Status.SUCCEEDED,
        releases=list(catalog.releases),
        latest_release=catalog.latest_release,
        catalog_url=catalog.catalog_url,
        manifest_sha256=catalog.manifest_sha256,
    )
    return {
        "status": "SUCCEEDED",
        "releases": list(catalog.releases),
        "latest_release": catalog.latest_release,
        "catalog_url": catalog.catalog_url,
        "manifest_sha256": catalog.manifest_sha256,
    }


@shared_task(
    name="overture.sync_snapshot",
    queue="maintenance",
    soft_time_limit=21_300,
    time_limit=21_600,
)  # type: ignore[untyped-decorator]
def sync_snapshot(release_id: str = "", province_code: str = "02") -> str:
    try:
        if province_code == "02":
            snapshot = sync_catalog(release_id=release_id or None)
        else:
            snapshot = sync_catalog(
                release_id=release_id or None,
                province_code=province_code,
            )
    except Exception as import_error:
        # A failed import is immutable diagnostic evidence and is never purged.
        # Retention may still remove older unreferenced SUPERSEDED snapshots.
        try:
            purge_retired_snapshots()
        except Exception as cleanup_error:
            import_error.add_note(
                f"Overture retention also failed: {type(cleanup_error).__name__}."
            )
        raise
    purge_retired_snapshots()
    return str(snapshot.pk)
