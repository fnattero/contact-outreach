from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.campaigns.models import Campaign, OutboundMessage
from apps.configuration.models import SearchCategory, SearchZone
from apps.integrations.contracts import SearchRequest
from apps.integrations.factory import get_extractor_provider
from apps.mailbox.models import InboundMessage
from apps.mailbox.tasks import deliver_outbound_messages
from contact_outreach.tasks import healthcheck


@pytest.mark.e2e
@pytest.mark.django_db
def test_owner_dashboard_fake_provider_and_worker_flow(client: Client) -> None:
    owner = User.objects.create_user(username="owner", password="correct-password")
    assert client.login(username="owner", password="correct-password")
    assert client.get(reverse("dashboard")).status_code == 200

    configured = client.post(
        reverse("integrations"),
        {
            "extractor_provider": "fake",
            "outscraper_api_key": "",
            "outscraper_base_url": "https://api.outscraper.cloud",
            "outscraper_max_cost_per_result": "0.010000",
            "outscraper_batch_size": "20",
            "outscraper_poll_seconds": "30",
            "llm_provider": "fake",
            "llm_model": "fake-deterministic",
            "ollama_base_url": "http://127.0.0.1:11434",
            "openai_compatible_base_url": "",
            "llm_api_key": "",
            "gmail_provider": "fake",
            "gmail_oauth_client_id": "",
            "gmail_oauth_client_secret": "",
            "current_password": "correct-password",
        },
    )
    assert configured.status_code == 302

    batch = get_extractor_provider(owner_id=owner.pk).extract(
        SearchRequest(query="demo", correlation_id="e2e", idempotency_key="e2e")
    )
    assert batch.records
    assert healthcheck.apply().get()["status"] == "ok"


@pytest.mark.e2e
@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_full_fake_acceptance_flow_from_ui(
    client: Client, owner: User, private_catalog_dir: object
) -> None:
    del private_catalog_dir
    assert client.login(username=owner.username, password="correct-password")

    profile = client.post(
        reverse("business-profile"),
        {
            "company_name": "Carbones Demo",
            "salesperson_name": "Fran",
            "phone": "",
            "whatsapp": "",
            "description": "Proveedor industrial",
            "products": "Carbones para motores",
            "differentiators": "Atención directa",
            "address": "CABA",
            "website": "",
            "signature": "Fran · Carbones Demo",
            "additional_instructions": "",
            "relevance_threshold": "70",
        },
    )
    assert profile.status_code == 302
    catalog_upload = client.post(
        reverse("catalogs"),
        {
            "name": "Catálogo fake",
            "file": SimpleUploadedFile(
                "catalogo.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
                content_type="application/pdf",
            ),
        },
    )
    assert catalog_upload.status_code == 302

    connected = client.post(reverse("gmail-connect"), follow=True)
    assert connected.status_code == 200
    assert "Cuenta Gmail conectada" in connected.content.decode()
    tested = client.post(reverse("gmail-test"), follow=True)
    assert "Prueba enviada" in tested.content.decode()

    category = SearchCategory.objects.get(name="Bobinados de motores")
    zone = SearchZone.objects.get(name="Palermo")
    catalog = owner.catalogs.get()
    created = client.post(
        reverse("campaign-create"),
        {
            "name": "Aceptación fake UI",
            "delivery_mode": "LIVE",
            "confirm_live": "on",
            "location_text": "Ciudad Autónoma de Buenos Aires, Argentina",
            "objective": "1",
            "max_raw_records": "10",
            "cost_limit": "1.00",
            "cost_currency": "USD",
            "daily_limit": "30",
            "message_interval_minutes": "1",
            "weekdays": ["0", "1", "2", "3", "4", "5", "6"],
            "window_start": "00:01",
            "window_end": "23:59",
            "timezone_name": "America/Argentina/Buenos_Aires",
            "relevance_threshold": "70",
            "extractor_provider": "fake",
            "llm_provider": "fake",
            "llm_base_url": "",
            "llm_model": "fake-deterministic",
            "catalog": str(catalog.pk),
            "categories": [str(category.pk)],
            "zones": [str(zone.pk)],
        },
    )
    assert created.status_code == 302
    campaign = Campaign.objects.get(name="Aceptación fake UI")
    started = client.post(reverse("campaign-action", args=(campaign.pk, "start")), follow=True)
    assert started.status_code == 200
    assert campaign.prospects.filter(pipeline_state="QUEUED").exists()
    message = campaign.messages.get(kind=OutboundMessage.Kind.FIRST_CONTACT)
    assert message.state == OutboundMessage.State.PREPARED

    deliver_outbound_messages()
    message.refresh_from_db()
    assert message.state == OutboundMessage.State.SENT

    simulated = client.post(
        reverse("gmail-fake-inbound"),
        {"outbound_id": str(message.pk), "scenario": "INTERESTED"},
        follow=True,
    )
    assert simulated.status_code == 200
    assert "Interesado" in simulated.content.decode()
    inbound = InboundMessage.objects.get(related_outbound=message)

    thread = client.get(reverse("response-thread", args=(inbound.pk,)))
    request_key = thread.context["form"].initial["idempotency_key"]
    replied = client.post(
        reverse("manual-reply", args=(inbound.pk,)),
        {
            "body_text": "Gracias. Te escribo para coordinar la visita.",
            "idempotency_key": request_key,
        },
        follow=True,
    )
    assert replied.status_code == 200
    manual = campaign.messages.get(kind=OutboundMessage.Kind.MANUAL_REPLY)
    assert manual.state == OutboundMessage.State.SENT
    assert client.get(reverse("prospects-export")).status_code == 200
    assert client.get(reverse("outbound-export")).status_code == 200
    assert client.get(reverse("responses-export")).status_code == 200
