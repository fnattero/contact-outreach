from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from tests.apps.api.test_prospects import _campaign, _prospect

from apps.campaigns.models import Campaign, OutboundMessage
from apps.catalogs.services import create_catalog
from apps.dashboard.csv_export import spreadsheet_safe
from apps.prospects.models import Prospect


@pytest.fixture
def audience(owner: User, private_catalog_dir: object) -> dict[str, Prospect]:
    del private_catalog_dir
    catalog = create_catalog(
        name="Exportaciones",
        upload=SimpleUploadedFile(
            "catalogo.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
            content_type="application/pdf",
        ),
        actor=owner,
    )
    first = _campaign(owner, catalog, "Campaña uno")
    second = _campaign(owner, catalog, "Campaña dos")
    return {
        "formula": _prospect(
            first,
            "=Taller Fórmula",
            category="Motores",
            state=Prospect.PipelineState.QUEUED,
            email="uno@taller.example",
        ),
        "other": _prospect(
            second,
            "Taller Ajeno",
            category="Motores",
            state=Prospect.PipelineState.QUEUED,
            email="otro@taller.example",
        ),
    }


def _csv(response) -> str:
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    return response.content.decode("utf-8-sig")


@pytest.mark.django_db
def test_spreadsheet_formulas_are_neutralized() -> None:
    assert spreadsheet_safe("+SUM(1,1)") == "'+SUM(1,1)"
    assert spreadsheet_safe("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
    assert spreadsheet_safe("@evil") == "'@evil"
    assert spreadsheet_safe("normal") == "normal"


@pytest.mark.django_db
def test_prospect_export_neutralizes_formulas_and_honours_filters(
    owner: User, audience: dict[str, Prospect]
) -> None:
    client = Client()
    client.force_login(owner)
    url = reverse("api-prospect-export")

    everything = _csv(client.get(url))
    assert "'=Taller Fórmula" in everything
    assert "Taller Ajeno" in everything

    only_first = _csv(client.get(url, {"campaign": str(audience["formula"].campaign_id)}))
    assert "'=Taller Fórmula" in only_first
    assert "uno@taller.example" in only_first
    assert "Taller Ajeno" not in only_first
    assert "otro@taller.example" not in only_first

    searched = _csv(client.get(url, {"q": "ajeno"}))
    assert "Taller Ajeno" in searched
    assert "Fórmula" not in searched


@pytest.mark.django_db
def test_prospect_export_rejects_invalid_filters(
    owner: User, audience: dict[str, Prospect]
) -> None:
    client = Client()
    client.force_login(owner)

    response = client.get(reverse("api-prospect-export"), {"campaign": "no-es-uuid"})

    assert response.status_code == 400


@pytest.mark.django_db
def test_outbound_export_neutralizes_formulas_in_message_fields(
    owner: User, audience: dict[str, Prospect]
) -> None:
    prospect = audience["formula"]
    OutboundMessage.objects.create(
        campaign=prospect.campaign,
        prospect=prospect,
        kind=OutboundMessage.Kind.INITIAL,
        recipient="uno@taller.example",
        recipient_normalized="uno@taller.example",
        subject='=HYPERLINK("http://evil.invalid")',
        body_text="Cuerpo",
        state=OutboundMessage.State.SENT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="export-formula",
        sent_at=timezone.now(),
    )
    client = Client()
    client.force_login(owner)

    exported = _csv(client.get(reverse("api-outbound-message-export")))

    assert "'=HYPERLINK" in exported
    assert ",=HYPERLINK" not in exported


@pytest.mark.django_db
def test_exports_are_not_available_to_a_seller(owner: User, audience: dict[str, Prospect]) -> None:
    seller = User.objects.create_user(username="export-seller", password="seller-password-1")
    client = Client()
    client.force_login(seller)

    for name in (
        "api-prospect-export",
        "api-outbound-message-export",
        "api-inbound-message-export",
    ):
        assert client.get(reverse(name)).status_code == 403, name
    assert Client().get(reverse("api-prospect-export")).status_code == 401
