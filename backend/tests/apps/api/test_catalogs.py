from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Membership


def _csrf(client: Client) -> str:
    return str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])


@pytest.mark.django_db
def test_catalog_upload_and_download_are_admin_only_and_private(
    owner: User, private_catalog_dir
) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf_token = _csrf(client)
    pdf = SimpleUploadedFile(
        "customer-catalog.pdf",
        b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
        content_type="application/pdf",
    )

    created = client.post(
        reverse("api-catalogs"),
        data={"name": "Customer catalog", "file": pdf},
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert created.status_code == 201
    catalog = created.json()["data"]
    assert catalog["detected_mime"] == "application/pdf"
    assert catalog["byte_size"] == len(pdf_content := b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF")
    assert catalog["sha256"]
    assert list(private_catalog_dir.rglob("*.pdf"))

    listed = client.get(reverse("api-catalogs"))
    assert listed.status_code == 200
    assert listed.json()["data"][0]["id"] == catalog["id"]

    download = client.get(reverse("api-catalog-download", args=(catalog["id"],)))
    assert download.status_code == 200
    assert download["Content-Type"] == "application/pdf"
    assert download["Cache-Control"] == "private, no-store"
    assert b"".join(download.streaming_content) == pdf_content

    vendor = User.objects.create_user(username="catalog-vendor", password="vendor-password-1234")
    assert vendor.membership.role == Membership.Role.VENDEDOR
    vendor_client = Client()
    vendor_client.force_login(vendor)
    assert vendor_client.get(reverse("api-catalogs")).status_code == 403
    assert (
        vendor_client.get(reverse("api-catalog-download", args=(catalog["id"],))).status_code == 403
    )


@pytest.mark.django_db
def test_catalog_rejects_non_pdf_before_writing(private_catalog_dir, owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    pdf = SimpleUploadedFile("not-a-pdf.pdf", b"not pdf", content_type="application/pdf")

    response = client.post(
        reverse("api-catalogs"),
        data={"name": "Invalid", "file": pdf},
        HTTP_X_CSRFTOKEN=_csrf(client),
    )

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert list(private_catalog_dir.rglob("*.pdf")) == []
