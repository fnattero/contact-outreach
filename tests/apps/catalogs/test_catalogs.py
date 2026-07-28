from __future__ import annotations

import errno
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.catalogs.models import Catalog
from apps.catalogs.services import create_catalog, verify_catalog
from apps.catalogs.storage import private_catalog_storage


def pdf_upload(
    *,
    name: str = "catalogo.pdf",
    content: bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
    content_type: str = "application/pdf",
) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type=content_type)


@pytest.mark.django_db
def test_catalog_upload_rejects_low_disk_without_partial_file(
    owner: User, private_catalog_dir: Path
) -> None:
    usage = namedtuple("usage", "total used free")(100, 99, 1)
    with patch("apps.catalogs.services.shutil.disk_usage", return_value=usage):
        with pytest.raises(ValidationError, match="espacio seguro"):
            create_catalog(name="Sin espacio", upload=pdf_upload(), actor=owner)
    assert not any(private_catalog_dir.rglob("*.pdf"))


@pytest.mark.django_db
def test_catalog_upload_removes_file_created_before_enospc(
    owner: User, private_catalog_dir: Path
) -> None:
    def partial_write(name: str, content: object) -> str:
        del content
        path = Path(private_catalog_storage.path(name))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-partial")
        raise OSError(errno.ENOSPC, "disk full")

    with patch.object(private_catalog_storage, "_save", side_effect=partial_write):
        with pytest.raises(ValidationError, match="sin espacio"):
            create_catalog(name="Disco lleno", upload=pdf_upload(), actor=owner)

    assert not any(private_catalog_dir.rglob("*.pdf"))
    assert not Catalog.objects.exists()


@pytest.mark.django_db
def test_valid_pdf_is_private_hashed_versioned_and_audited(
    owner: User, private_catalog_dir: Path
) -> None:
    first = create_catalog(
        name="Catálogo industrial", upload=pdf_upload(name="../mal nombre.pdf"), actor=owner
    )
    second = create_catalog(
        name="Catálogo industrial",
        upload=pdf_upload(content=b"%PDF-1.7\n2 0 obj\n<<>>\nendobj\n%%EOF"),
        actor=owner,
    )
    assert first.version == 1
    assert second.version == 2
    assert first.original_filename == "mal_nombre.pdf"
    assert first.detected_mime == "application/pdf"
    assert len(first.sha256) == 64
    assert Path(first.file.path).is_relative_to(private_catalog_dir)
    assert AuditEvent.objects.filter(action="catalog.created").count() == 2
    verify_catalog(first)
    first.name = "Editado"
    with pytest.raises(ValidationError, match="inmutable"):
        first.save()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("upload", "message"),
    [
        (pdf_upload(name="catalogo.txt"), "extensión"),
        (pdf_upload(content=b"not a pdf\n%%EOF"), "tipo de archivo detectado"),
        (pdf_upload(content=b"%PDF-1.4\nno eof"), "estructura"),
        (pdf_upload(content=b""), "vacío"),
    ],
)
def test_invalid_pdf_is_rejected(
    owner: User, private_catalog_dir: Path, upload: SimpleUploadedFile, message: str
) -> None:
    del private_catalog_dir
    with pytest.raises(ValidationError, match=message):
        create_catalog(name="Catálogo", upload=upload, actor=owner)
    assert not Catalog.objects.exists()


@pytest.mark.django_db
def test_pdf_mime_is_detected_from_bytes_not_client_metadata(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = create_catalog(
        name="MIME detectado",
        upload=pdf_upload(content_type="application/octet-stream"),
        actor=owner,
    )
    assert catalog.detected_mime == "application/pdf"


@pytest.mark.django_db
def test_oversized_and_duplicate_pdf_are_rejected(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    oversized = pdf_upload(content=b"%PDF-1.4\n" + b"x" * settings.CATALOG_MAX_BYTES + b"%%EOF")
    with pytest.raises(ValidationError, match="15 MiB"):
        create_catalog(name="Grande", upload=oversized, actor=owner)
    create_catalog(name="Único", upload=pdf_upload(), actor=owner)
    with pytest.raises(ValidationError, match="ya fue cargado"):
        create_catalog(name="Duplicado", upload=pdf_upload(), actor=owner)


@pytest.mark.django_db
def test_catalog_integrity_detects_tampering(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    catalog = create_catalog(name="Catálogo", upload=pdf_upload(), actor=owner)
    with open(catalog.file.path, "ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(ValidationError, match="integridad"):
        verify_catalog(catalog)


@pytest.mark.django_db
def test_tampered_or_missing_catalog_download_returns_controlled_unavailable_response(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    client.force_login(owner)
    catalog = create_catalog(name="Catálogo alterado", upload=pdf_upload(), actor=owner)
    with open(catalog.file.path, "ab") as handle:
        handle.write(b"tampered")

    tampered = client.get(reverse("catalog-download", args=(catalog.pk,)))

    assert tampered.status_code == 404
    assert "no disponible" in tampered.content.decode()

    Path(catalog.file.path).unlink()
    missing = client.get(reverse("catalog-download", args=(catalog.pk,)))
    assert missing.status_code == 404


@pytest.mark.django_db
def test_catalog_upload_and_authenticated_download_views(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    assert client.get(reverse("catalogs")).status_code == 302
    client.force_login(owner)
    response = client.post(reverse("catalogs"), {"name": "Web", "file": pdf_upload(name="web.pdf")})
    assert response.status_code == 302
    catalog = Catalog.objects.get(name="Web")
    download = client.get(reverse("catalog-download", args=(catalog.pk,)))
    assert download.status_code == 200
    assert download["Content-Type"] == "application/pdf"
    assert b"".join(download.streaming_content).startswith(b"%PDF-")


@pytest.mark.django_db
def test_catalog_view_shows_validation_error(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    client.force_login(owner)
    response = client.post(
        reverse("catalogs"), {"name": "Falso", "file": pdf_upload(content=b"fake")}
    )
    assert response.status_code == 200
    assert "tipo de archivo detectado" in response.content.decode()
