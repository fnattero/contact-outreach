from __future__ import annotations

import hashlib
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils.text import get_valid_filename

from apps.audit.services import record_event
from apps.catalogs.models import Catalog

PDF_MIME = "application/pdf"


def _detect_mime(content: bytes) -> str:
    if content.startswith(b"%PDF-"):
        return PDF_MIME
    return "application/octet-stream"


def _read_upload(upload: UploadedFile) -> tuple[str, int, str]:
    size = upload.size
    if size is None or size <= 0:
        raise ValidationError("El PDF está vacío.")
    if size > settings.CATALOG_MAX_BYTES:
        raise ValidationError("El PDF supera el máximo permitido de 15 MiB.")
    if not upload.name or Path(upload.name).suffix.casefold() != ".pdf":
        raise ValidationError("El archivo debe tener extensión .pdf.")
    content = upload.read()
    upload.seek(0)
    if len(content) != size:
        raise ValidationError("No se pudo leer el PDF completo.")
    detected_mime = _detect_mime(content)
    if detected_mime != PDF_MIME:
        raise ValidationError("El tipo MIME detectado del archivo no es PDF.")
    if not content.startswith(b"%PDF-"):
        raise ValidationError("El archivo no tiene un encabezado PDF válido.")
    if b"%%EOF" not in content[-2048:]:
        raise ValidationError("El archivo no tiene una estructura PDF válida.")
    return hashlib.sha256(content).hexdigest(), size, detected_mime


@transaction.atomic
def create_catalog(*, name: str, upload: UploadedFile, actor: User) -> Catalog:
    clean_name = re.sub(r"\s+", " ", name.strip())
    if not clean_name:
        raise ValidationError("Ingresá un nombre para el catálogo.")
    digest, size, detected_mime = _read_upload(upload)
    if Catalog.objects.filter(sha256=digest).exists():
        raise ValidationError("Este mismo PDF ya fue cargado.")
    existing = Catalog.objects.select_for_update().filter(name=clean_name)
    last_version = existing.order_by("-version").values_list("version", flat=True).first()
    original_filename = get_valid_filename(Path(upload.name or "catalog.pdf").name)[:255]
    catalog = Catalog(
        name=clean_name,
        version=(last_version or 0) + 1,
        original_filename=original_filename,
        detected_mime=detected_mime,
        byte_size=size,
        sha256=digest,
        uploaded_by=actor,
    )
    catalog.file.save("catalog.pdf", upload, save=False)
    try:
        catalog.full_clean()
        catalog.save()
    except Exception:
        catalog.file.delete(save=False)
        raise
    record_event(
        action="catalog.created",
        entity=catalog,
        actor=actor,
        after={
            "name": catalog.name,
            "version": catalog.version,
            "byte_size": catalog.byte_size,
            "sha256": catalog.sha256,
        },
    )
    return catalog


def verify_catalog(catalog: Catalog) -> None:
    if catalog.missing or not catalog.active or not catalog.file.storage.exists(catalog.file.name):
        raise ValidationError("El catálogo seleccionado no está disponible.")
    with catalog.file.open("rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    if digest != catalog.sha256 or catalog.file.size != catalog.byte_size:
        raise ValidationError("El catálogo seleccionado perdió integridad.")
