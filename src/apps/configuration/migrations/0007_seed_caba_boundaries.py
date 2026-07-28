from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path

from django.db import migrations


SOURCE_URL = (
    "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/"
    "innovacion-transformacion-digital/barrios/barrios.geojson"
)
ATTRIBUTION = "Buenos Aires Data · Barrios · CC-BY-2.5-AR"


def comparable_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).casefold()


def coordinate_pairs(value):
    if (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        yield float(value[0]), float(value[1])
        return
    if isinstance(value, list):
        for child in value:
            yield from coordinate_pairs(child)


def seed_boundaries(apps, schema_editor) -> None:
    del schema_editor
    SearchZone = apps.get_model("configuration", "SearchZone")
    asset = Path(__file__).resolve().parents[1] / "data" / "caba_barrios.geojson"
    payload = json.loads(asset.read_text(encoding="utf-8"))
    zones = {
        comparable_name(zone.name): zone
        for zone in SearchZone.objects.filter(kind="NEIGHBORHOOD", archived_at__isnull=True)
    }
    aliases = {
        "paternal": "la paternal",
        "villa gral. mitre": "villa general mitre",
    }
    for feature in payload["features"]:
        source_name = comparable_name(str(feature["properties"]["nombre"]))
        zone = zones.get(aliases.get(source_name, source_name))
        if zone is None:
            continue
        geometry = feature["geometry"]
        canonical = json.dumps(
            geometry,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        coordinates = list(coordinate_pairs(geometry["coordinates"]))
        longitudes = [pair[0] for pair in coordinates]
        latitudes = [pair[1] for pair in coordinates]
        zone.boundary_geojson = geometry
        zone.boundary_bbox = [
            min(longitudes),
            min(latitudes),
            max(longitudes),
            max(latitudes),
        ]
        zone.boundary_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        zone.boundary_source = SOURCE_URL
        zone.boundary_attribution = ATTRIBUTION
        zone.save(
            update_fields=(
                "boundary_geojson",
                "boundary_bbox",
                "boundary_hash",
                "boundary_source",
                "boundary_attribution",
                "updated_at",
            )
        )


class Migration(migrations.Migration):
    dependencies = [("configuration", "0006_overture_search_configuration")]
    operations = [migrations.RunPython(seed_boundaries, migrations.RunPython.noop)]
