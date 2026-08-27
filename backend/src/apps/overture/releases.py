from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from django.core.exceptions import ValidationError

OFFICIAL_STAC_URL = "https://stac.overturemaps.org/catalog.json"
OFFICIAL_STAC_HOST = "stac.overturemaps.org"
OFFICIAL_S3_BUCKET = "overturemaps-us-west-2"
MAX_CATALOG_BYTES = 1024 * 1024
MAX_RELEASE_CATALOG_BYTES = 256 * 1024
MAX_RELEASES = 240
RELEASE_PATTERN = re.compile(r"^20\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\.\d+$")
SCHEMA_VERSION_PATTERN = re.compile(r"^v?\d{1,3}\.\d{1,3}\.\d{1,3}(?:[-+][A-Za-z0-9.-]+)?$")

# The release-level STAC catalogs currently expose ``schema:version`` as null.
# Keep the schema contract pinned to versions reviewed against the official
# Overture schema/release notes. A new release must be reviewed and added here
# before it can be imported; guessing from a date or silently accepting drift
# would make the importer fail open.
REVIEWED_RELEASE_SCHEMA_VERSIONS: Mapping[str, str] = {
    "2026-06-17.0": "v1.17.0",
    "2026-07-22.0": "v1.18.0",
}


class ReleaseDiscoveryError(RuntimeError):
    pass


class CatalogTransport(Protocol):
    def __call__(self, url: str, *, timeout_seconds: float, max_bytes: int) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ReleaseCatalog:
    releases: tuple[str, ...]
    latest_release: str
    catalog_url: str
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class ReleaseDescriptor:
    release_id: str
    schema_version: str
    taxonomy_version: str
    source_uri: str
    catalog_url: str
    manifest_sha256: str


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def validate_release_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError("El identificador de release Overture es inválido.")
    release_id = value.strip()
    if not RELEASE_PATTERN.fullmatch(release_id):
        raise ValidationError("El identificador de release Overture es inválido.")
    return release_id


def official_places_source_uri(release_id: str) -> str:
    validated = validate_release_id(release_id)
    return f"s3://{OFFICIAL_S3_BUCKET}/release/{validated}/theme=places/type=place/"


def official_release_catalog_url(release_id: str) -> str:
    validated = validate_release_id(release_id)
    return f"https://{OFFICIAL_STAC_HOST}/{validated}/catalog.json"


def _validate_official_stac_url(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != OFFICIAL_STAC_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise ReleaseDiscoveryError("El catálogo Overture debe usar el host STAC oficial.")
    return url


def _official_transport(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
    _validate_official_stac_url(url)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "contact-outreach/0.1"},
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            final_url = response.geturl()
            _validate_official_stac_url(final_url)
            if final_url != url:
                raise ReleaseDiscoveryError("El catálogo Overture intentó redirigir la solicitud.")
            status = getattr(response, "status", 200)
            if status != 200:
                raise ReleaseDiscoveryError("El catálogo Overture respondió con un error.")
            raw_payload = response.read(max_bytes + 1)
            if not isinstance(raw_payload, bytes):
                raise ReleaseDiscoveryError("El catálogo Overture devolvió bytes inválidos.")
            payload = raw_payload
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise ReleaseDiscoveryError("No se pudo consultar el catálogo Overture oficial.") from exc
    if len(payload) > max_bytes:
        raise ReleaseDiscoveryError("El catálogo Overture supera el tamaño permitido.")
    return payload


def _release_from_child_href(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseDiscoveryError("El catálogo Overture contiene un enlace child inválido.")
    absolute = urljoin(OFFICIAL_STAC_URL, value.strip())
    _validate_official_stac_url(absolute)
    parts = [part for part in urlsplit(absolute).path.split("/") if part]
    if len(parts) < 2 or parts[-1] != "catalog.json":
        raise ReleaseDiscoveryError("El catálogo Overture contiene un enlace child inválido.")
    try:
        return validate_release_id(parts[-2])
    except ValidationError as exc:
        raise ReleaseDiscoveryError("El catálogo Overture contiene un release inválido.") from exc


def _parse_catalog(payload: bytes) -> ReleaseCatalog:
    if len(payload) > MAX_CATALOG_BYTES:
        raise ReleaseDiscoveryError("El catálogo Overture supera el tamaño permitido.")
    try:
        raw = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseDiscoveryError("El catálogo Overture no contiene JSON válido.") from exc
    if not isinstance(raw, Mapping):
        raise ReleaseDiscoveryError("El catálogo Overture tiene una estructura inválida.")
    raw_links = raw.get("links")
    if isinstance(raw_links, (str, bytes)) or not isinstance(raw_links, Sequence):
        raise ReleaseDiscoveryError("El catálogo Overture no contiene enlaces válidos.")
    releases: list[str] = []
    for raw_link in raw_links:
        if not isinstance(raw_link, Mapping) or raw_link.get("rel") != "child":
            continue
        releases.append(_release_from_child_href(raw_link.get("href")))
        if len(releases) > MAX_RELEASES:
            raise ReleaseDiscoveryError("El catálogo Overture contiene demasiados releases.")
    if not releases or len(set(releases)) != len(releases):
        raise ReleaseDiscoveryError("El catálogo Overture contiene releases inválidos.")
    try:
        latest = validate_release_id(raw.get("latest"))
    except ValidationError as exc:
        raise ReleaseDiscoveryError("El catálogo Overture no declara un latest válido.") from exc
    if latest not in releases:
        raise ReleaseDiscoveryError("El latest Overture no aparece entre los releases oficiales.")
    return ReleaseCatalog(
        releases=tuple(releases),
        latest_release=latest,
        catalog_url=OFFICIAL_STAC_URL,
        manifest_sha256=hashlib.sha256(payload).hexdigest(),
    )


def discover_releases(
    *,
    transport: CatalogTransport | Callable[..., bytes] | None = None,
    timeout_seconds: float = 15.0,
) -> ReleaseCatalog:
    if timeout_seconds <= 0 or timeout_seconds > 60:
        raise ValidationError("El timeout de Overture debe estar entre 0 y 60 segundos.")
    fetch = transport or _official_transport
    payload = fetch(
        OFFICIAL_STAC_URL,
        timeout_seconds=timeout_seconds,
        max_bytes=MAX_CATALOG_BYTES,
    )
    if not isinstance(payload, bytes):
        raise ReleaseDiscoveryError("El transporte Overture devolvió una respuesta inválida.")
    return _parse_catalog(payload)


def discover_release(
    release_id: str | None = None,
    *,
    transport: CatalogTransport | Callable[..., bytes] | None = None,
) -> ReleaseDescriptor:
    catalog = discover_releases(transport=transport)
    selected = catalog.latest_release if release_id is None else validate_release_id(release_id)
    if selected not in catalog.releases:
        raise ReleaseDiscoveryError(
            "El release solicitado no está en el catálogo Overture oficial."
        )
    release_catalog_url = official_release_catalog_url(selected)
    fetch = transport or _official_transport
    release_payload = fetch(
        release_catalog_url,
        timeout_seconds=15.0,
        max_bytes=MAX_RELEASE_CATALOG_BYTES,
    )
    if not isinstance(release_payload, bytes) or len(release_payload) > MAX_RELEASE_CATALOG_BYTES:
        raise ReleaseDiscoveryError("El catálogo del release Overture es inválido.")
    try:
        release_metadata = json.loads(release_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseDiscoveryError("El catálogo del release Overture no contiene JSON.") from exc
    if not isinstance(release_metadata, Mapping) or release_metadata.get("id") != selected:
        raise ReleaseDiscoveryError("El catálogo Overture no coincide con el release solicitado.")
    reviewed_schema_version = REVIEWED_RELEASE_SCHEMA_VERSIONS.get(selected)
    if reviewed_schema_version is None:
        raise ReleaseDiscoveryError(
            "El release Overture todavía no fue revisado contra una versión de esquema compatible."
        )
    declared_schema_version = release_metadata.get("schema:version")
    if declared_schema_version is None:
        schema_version = reviewed_schema_version
    elif isinstance(declared_schema_version, str) and SCHEMA_VERSION_PATTERN.fullmatch(
        declared_schema_version
    ):
        schema_version = (
            declared_schema_version
            if declared_schema_version.startswith("v")
            else f"v{declared_schema_version}"
        )
        if schema_version != reviewed_schema_version:
            raise ReleaseDiscoveryError(
                "El esquema declarado por Overture no coincide con la versión revisada."
            )
    else:
        raise ReleaseDiscoveryError("El release Overture declara una versión de esquema inválida.")
    return ReleaseDescriptor(
        release_id=selected,
        schema_version=schema_version,
        # Overture publishes taxonomy content with each data release, without a
        # separate semantic taxonomy version in STAC. The release ID is therefore
        # the exact immutable taxonomy version used by this snapshot.
        taxonomy_version=selected,
        source_uri=official_places_source_uri(selected),
        catalog_url=release_catalog_url,
        manifest_sha256=hashlib.sha256(release_payload).hexdigest(),
    )


def discover_latest_release(
    *,
    transport: CatalogTransport | Callable[..., bytes] | None = None,
) -> ReleaseDescriptor:
    return discover_release(transport=transport)
