from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q
from shapely import make_valid
from shapely.geometry import mapping, shape


PROVINCES_ASSET = "argentina_provincias_georef_2026.geojson"
DISTRICTS_ASSET = "argentina_departamentos_georef_2026.geojson"
PROVINCES_SHA256 = "b3530d283d992f828b41993a8fb8b7a4e643408a3f76451340ed866a349655ab"
DISTRICTS_SHA256 = "fc10b1dc734f3a7c274f5a39e6ae8749c4a61789a62fc0184724d6acda9d87fb"
GEOREF_PROVINCES_URL = "https://apis.datos.gob.ar/georef/api/provincias.geojson"
GEOREF_DISTRICTS_URL = "https://apis.datos.gob.ar/georef/api/departamentos.geojson"
GEOREF_ATTRIBUTION = (
    "Servicio de Normalización de Datos Geográficos de Argentina (GeoRef) · "
    "Instituto Geográfico Nacional (IGN)"
)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip()).casefold()


def _comparable(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).casefold()


def _coordinates(value):
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
            yield from _coordinates(child)


def _canonical_coordinates(value):
    if (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        longitude = float(value[0])
        latitude = float(value[1])
        # GeoRef's Tierra del Fuego/Antarctica geometry contains a handful of
        # coordinates a few nanodegrees beyond -90 because of source precision.
        # Clamp only that harmless floating-point spill; reject material drift.
        if not (-180.000001 <= longitude <= 180.000001) or not (
            -90.000001 <= latitude <= 90.000001
        ):
            raise RuntimeError("El seed GeoRef contiene una coordenada fuera de WGS84.")
        return [
            min(180.0, max(-180.0, longitude)),
            min(90.0, max(-90.0, latitude)),
            *value[2:],
        ]
    if isinstance(value, list):
        return [_canonical_coordinates(child) for child in value]
    return value


def _geometry_values(geometry):
    geometry = {
        **geometry,
        "coordinates": _canonical_coordinates(geometry["coordinates"]),
    }
    shaped = shape(geometry)
    if not shaped.is_valid:
        shaped = make_valid(shaped)
        if shaped.geom_type not in {"Polygon", "MultiPolygon"}:
            raise RuntimeError("El seed GeoRef no pudo normalizarse como área poligonal.")
        geometry = json.loads(json.dumps(mapping(shaped), ensure_ascii=False))
    canonical = json.dumps(
        geometry,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    pairs = list(_coordinates(geometry["coordinates"]))
    if not pairs:
        raise RuntimeError("El seed GeoRef contiene una geometría vacía.")
    longitudes = [pair[0] for pair in pairs]
    latitudes = [pair[1] for pair in pairs]
    return (
        geometry,
        [min(longitudes), min(latitudes), max(longitudes), max(latitudes)],
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _load_asset(filename: str, expected_sha256: str):
    path = Path(__file__).resolve().parents[1] / "data" / filename
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise RuntimeError(f"El artefacto geográfico {filename} perdió integridad.")
    parsed = json.loads(payload)
    features = parsed.get("features") if isinstance(parsed, dict) else None
    if not isinstance(features, list):
        raise RuntimeError(f"El artefacto geográfico {filename} no contiene features.")
    return features


def seed_argentina_hierarchy(apps, schema_editor) -> None:
    del schema_editor
    Workspace = apps.get_model("accounts", "Workspace")
    SearchZone = apps.get_model("configuration", "SearchZone")

    workspace, _ = Workspace.objects.get_or_create(
        singleton_key=1,
        defaults={"name": "Mi empresa"},
    )
    SearchZone.objects.filter(workspace__isnull=True).update(workspace_id=workspace.pk)

    province_features = _load_asset(PROVINCES_ASSET, PROVINCES_SHA256)
    district_features = _load_asset(DISTRICTS_ASSET, DISTRICTS_SHA256)
    if len(province_features) != 24 or len(district_features) != 529:
        raise RuntimeError("El seed GeoRef no contiene las 24 provincias y 529 divisiones.")

    provinces = {}
    for order, feature in enumerate(
        sorted(province_features, key=lambda item: str(item["properties"]["id"]))
    ):
        properties = feature["properties"]
        code = str(properties["id"])
        name = str(properties["nombre"])
        geometry, bbox, boundary_hash = _geometry_values(feature["geometry"])
        label_plural = (
            "Barrios" if code == "02" else ("Partidos" if code == "06" else "Departamentos")
        )
        province, _ = SearchZone.objects.update_or_create(
            workspace_id=workspace.pk,
            source="GEOREF",
            official_code=code,
            defaults={
                "name": name,
                "normalized_name": _normalize(name),
                "kind": "CUSTOM",
                "level": "PROVINCE",
                "parent_id": None,
                "province_code": code,
                "province_name": name,
                "selectable": False,
                "label_plural": label_plural,
                "location_text": f"{name}, Argentina",
                "boundary_geojson": geometry,
                "boundary_bbox": bbox,
                "boundary_hash": boundary_hash,
                "boundary_revision": 1,
                "boundary_source": GEOREF_PROVINCES_URL,
                "boundary_attribution": GEOREF_ATTRIBUTION,
                "active": True,
                "sort_order": order,
                "archived_at": None,
            },
        )
        provinces[code] = province

    category_plural = {
        "Partido": "Partidos",
        "Departamento": "Departamentos",
        "Comuna": "Comunas",
    }
    district_counts = {}
    for feature in district_features:
        properties = feature["properties"]
        province_data = properties["provincia"]
        province_code = str(province_data["id"])
        province = provinces[province_code]
        code = str(properties["id"])
        name = str(properties["nombre"])
        category = str(properties.get("categoria") or "Departamento")
        geometry, bbox, boundary_hash = _geometry_values(feature["geometry"])
        district_counts[province_code] = district_counts.get(province_code, 0) + 1
        SearchZone.objects.update_or_create(
            workspace_id=workspace.pk,
            source="GEOREF",
            parent_id=province.pk,
            official_code=code,
            defaults={
                "name": name,
                "normalized_name": _normalize(name),
                "kind": "CUSTOM",
                "level": "DISTRICT",
                "province_code": province_code,
                "province_name": province.name,
                # CABA keeps its official communes for reference, while its barrios
                # remain the selectable level used by campaigns.
                "selectable": province_code != "02",
                "label_plural": category_plural.get(category, "Departamentos"),
                "location_text": f"{name}, {province.name}, Argentina",
                "boundary_geojson": geometry,
                "boundary_bbox": bbox,
                "boundary_hash": boundary_hash,
                "boundary_revision": 1,
                "boundary_source": GEOREF_DISTRICTS_URL,
                "boundary_attribution": GEOREF_ATTRIBUTION,
                "active": True,
                "sort_order": district_counts[province_code] - 1,
                "archived_at": None,
            },
        )

    caba = provinces["02"]
    existing_neighborhoods = list(
        SearchZone.objects.filter(kind="NEIGHBORHOOD", archived_at__isnull=True)
    )
    by_name = {_comparable(zone.name): zone for zone in existing_neighborhoods}
    aliases = {
        "paternal": "la paternal",
        "villa gral. mitre": "villa general mitre",
    }
    caba_asset = Path(__file__).resolve().parents[1] / "data" / "caba_barrios.geojson"
    caba_features = json.loads(caba_asset.read_text(encoding="utf-8"))["features"]
    matched_ids = set()
    for order, feature in enumerate(caba_features):
        properties = feature["properties"]
        source_name = _comparable(str(properties["nombre"]))
        zone = by_name.get(aliases.get(source_name, source_name))
        if zone is None:
            raise RuntimeError(f"No se encontró el barrio CABA {properties['nombre']}.")
        matched_ids.add(zone.pk)
        zone.workspace_id = workspace.pk
        zone.official_code = f"02-BARRIO-{int(properties['id']):03d}"
        zone.level = "NEIGHBORHOOD"
        zone.parent_id = caba.pk
        zone.province_code = "02"
        zone.province_name = caba.name
        zone.selectable = True
        zone.label_plural = "Barrios"
        zone.source = "BUENOS_AIRES_DATA"
        zone.sort_order = order
        zone.save(
            update_fields=(
                "workspace",
                "official_code",
                "level",
                "parent",
                "province_code",
                "province_name",
                "selectable",
                "label_plural",
                "source",
                "sort_order",
                "updated_at",
            )
        )
    if len(matched_ids) != 48:
        raise RuntimeError("El seed no pudo preservar los 48 barrios de CABA.")

    # Any operator-created legacy zone remains available and receives a stable
    # installation-local code. No geometry or campaign snapshot is rewritten.
    for zone in SearchZone.objects.filter(official_code="").iterator():
        zone.workspace_id = workspace.pk
        zone.official_code = f"custom-{zone.pk}"
        zone.level = "CUSTOM"
        zone.selectable = True
        zone.source = "LEGACY"
        zone.save(
            update_fields=(
                "workspace",
                "official_code",
                "level",
                "selectable",
                "source",
                "updated_at",
            )
        )


def unseed_argentina_hierarchy(apps, schema_editor) -> None:
    """Remove only the hierarchy introduced by this migration on rollback.

    The original CABA neighborhoods and operator-created zones predate this
    migration and must survive. Clearing their parent first lets the seeded
    province rows be removed without violating the protective relationship.
    """
    del schema_editor
    SearchZone = apps.get_model("configuration", "SearchZone")
    SearchZone.objects.filter(
        level="NEIGHBORHOOD",
        source="BUENOS_AIRES_DATA",
    ).update(parent_id=None)
    SearchZone.objects.filter(level="DISTRICT", source="GEOREF").delete()
    SearchZone.objects.filter(level="PROVINCE", source="GEOREF").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("configuration", "0007_seed_caba_boundaries"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="searchzone",
            name="configuration_zone_active_name_unique",
        ),
        migrations.AddField(
            model_name="searchzone",
            name="workspace",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="search_zones",
                to="accounts.workspace",
            ),
        ),
        migrations.AddField(
            model_name="searchzone",
            name="official_code",
            field=models.CharField(default="", max_length=120),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="searchzone",
            name="level",
            field=models.CharField(
                choices=[
                    ("COUNTRY", "País"),
                    ("PROVINCE", "Provincia"),
                    ("DISTRICT", "Partido, departamento o comuna"),
                    ("NEIGHBORHOOD", "Barrio"),
                    ("CUSTOM", "Personalizada"),
                ],
                default="CUSTOM",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="searchzone",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="children",
                to="configuration.searchzone",
            ),
        ),
        migrations.AddField(
            model_name="searchzone",
            name="province_code",
            field=models.CharField(blank=True, db_index=True, max_length=20),
        ),
        migrations.AddField(
            model_name="searchzone",
            name="province_name",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="searchzone",
            name="selectable",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="searchzone",
            name="label_plural",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="searchzone",
            name="source",
            field=models.CharField(
                choices=[
                    ("GEOREF", "GeoRef / IGN"),
                    ("BUENOS_AIRES_DATA", "Buenos Aires Data"),
                    ("CUSTOM", "Carga manual"),
                    ("LEGACY", "Configuración anterior"),
                ],
                default="CUSTOM",
                max_length=30,
            ),
        ),
        migrations.RunPython(seed_argentina_hierarchy, unseed_argentina_hierarchy),
        migrations.AlterField(
            model_name="searchzone",
            name="workspace",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="search_zones",
                to="accounts.workspace",
            ),
        ),
        migrations.AlterModelOptions(
            name="searchzone",
            options={"ordering": ("province_name", "sort_order", "name")},
        ),
        migrations.AddConstraint(
            model_name="searchzone",
            constraint=models.UniqueConstraint(
                fields=("workspace", "parent", "official_code"),
                name="configuration_zone_hierarchy_code_unique",
                nulls_distinct=False,
            ),
        ),
        migrations.AddConstraint(
            model_name="searchzone",
            constraint=models.UniqueConstraint(
                condition=Q(archived_at__isnull=True),
                fields=("workspace", "parent", "normalized_name"),
                name="configuration_zone_sibling_name_unique",
                nulls_distinct=False,
            ),
        ),
        migrations.AddConstraint(
            model_name="searchzone",
            constraint=models.CheckConstraint(
                condition=Q(level="CUSTOM") | ~Q(official_code=""),
                name="configuration_official_zone_has_code",
            ),
        ),
        migrations.AddConstraint(
            model_name="searchzone",
            constraint=models.CheckConstraint(
                condition=(
                    Q(level__in=("COUNTRY", "PROVINCE", "CUSTOM"))
                    | (~Q(province_code="") & ~Q(province_name=""))
                ),
                name="configuration_local_zone_has_province",
            ),
        ),
    ]
