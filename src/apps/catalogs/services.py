from __future__ import annotations

import errno
import hashlib
import logging
import re
import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.storage import Storage
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils.text import get_valid_filename

from apps.audit.services import record_event
from apps.catalogs.models import Catalog
from apps.catalogs.storage import private_catalog_storage

PDF_MIME = "application/pdf"
logger = logging.getLogger(__name__)


def _storage_error(exc: OSError) -> ValidationError:
    error_code = "enospc" if exc.errno == errno.ENOSPC else "storage_unavailable"
    logger.error(
        "El almacenamiento privado no está disponible para escritura.",
        extra={"event": "storage.write_failed", "error_code": error_code},
    )
    if exc.errno == errno.ENOSPC:
        return ValidationError(
            "El almacenamiento se quedó sin espacio; no se guardó un catálogo parcial."
        )
    return ValidationError("No se pudo acceder al almacenamiento privado.")


def _delete_partial(storage: Storage, name: str) -> None:
    if not name:
        return
    try:
        storage.delete(name)
    except OSError:
        logger.error(
            "No se pudo limpiar un archivo parcial del almacenamiento privado.",
            extra={"event": "storage.partial_cleanup_failed"},
        )


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
    storage_root = Path(private_catalog_storage.location)
    try:
        storage_root.mkdir(parents=True, exist_ok=True)
        free_bytes = shutil.disk_usage(storage_root).free
    except OSError as exc:
        raise _storage_error(exc) from exc
    if free_bytes < size + settings.MIN_FREE_DISK_BYTES:
        raise ValidationError(
            "No hay espacio seguro para guardar el catálogo. Liberá disco y volvé a intentar."
        )
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
    target_name = catalog.file.field.generate_filename(catalog, "catalog.pdf")
    stored_name = ""
    try:
        stored_name = catalog.file.storage.save(target_name, upload)
        catalog.file.name = stored_name
        catalog.full_clean()
        catalog.save()
    except OSError as exc:
        _delete_partial(catalog.file.storage, stored_name or target_name)
        raise _storage_error(exc) from exc
    except Exception:
        _delete_partial(catalog.file.storage, stored_name or target_name)
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
