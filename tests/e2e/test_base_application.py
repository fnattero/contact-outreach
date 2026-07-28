from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.campaigns.models import Campaign, OutboundMessage
from apps.compliance.models import ContactLedger
from apps.configuration.models import SearchCategory, SearchZone
from apps.integrations.contracts import SearchRequest
from apps.integrations.factory import get_extractor_provider
from apps.integrations.fakes import FakeGmailProvider
from apps.integrations.llm import OllamaProvider, OpenAICompatibleProvider
from apps.integrations.website import HttpWebsiteFetcher
from apps.mailbox.models import FakeGmailMessage, GmailConnection, InboundMessage
from apps.mailbox.tasks import deliver_outbound_messages
from apps.overture.importer import OVERTURE_ATTRIBUTION
from apps.overture.matching import padded_name_search, taxonomy_codes_search
from apps.overture.models import (
    OvertureCoveragePartition,
    OvertureDatasetSnapshot,
    OvertureDatasetZone,
    OverturePlace,
    OverturePlaceZone,
    OvertureRelease,
)
from apps.overture.reader import OfficialOverturePlaceReader
from apps.overture.releases import official_places_source_uri
from apps.prospects.email_validation import MXStatus
from apps.prospects.models import Prospect, WebsiteSnapshot
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
            "overture_min_confidence": "0.750",
            "website_fetcher": "fake",
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

    batch = get_extractor_provider(owner_id=owner.pk).search(
        SearchRequest(query="demo", correlation_id="e2e", idempotency_key="e2e")
    )
    assert batch.records
    assert healthcheck.apply().get()["status"] == "ok"


@pytest.mark.e2e
@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_review_only_fake_flow_searches_drafts_and_exposes_content_without_gmail(
    client: Client,
    owner: User,
    private_catalog_dir: object,
    django_capture_on_commit_callbacks: Any,
) -> None:
    del private_catalog_dir
    assert client.login(username=owner.username, password="correct-password")
    assert (
        client.post(
            reverse("business-profile"),
            {
                "company_name": "Carbones Revisión",
                "salesperson_name": "Fran",
                "phone": "",
                "whatsapp": "",
                "description": "Proveedor industrial",
                "products": "Carbones para motores",
                "differentiators": "Atención directa",
                "address": "CABA",
                "website": "",
                "signature": "Fran · Carbones Revisión",
                "additional_instructions": "",
                "relevance_threshold": "70",
            },
        ).status_code
        == 302
    )
    assert (
        client.post(
            reverse("catalogs"),
            {
                "name": "Catálogo revisión",
                "file": SimpleUploadedFile(
                    "catalogo.pdf",
                    b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
                    content_type="application/pdf",
                ),
            },
        ).status_code
        == 302
    )
    category = SearchCategory.objects.get(name="Bobinados de motores")
    zone = SearchZone.objects.get(name="Palermo")
    catalog = owner.catalogs.get()
    created = client.post(
        reverse("campaign-create"),
        {
            "name": "Aceptación solo revisión",
            "delivery_mode": Campaign.DeliveryMode.REVIEW_ONLY,
            "approval_mode": Campaign.ApprovalMode.CAMPAIGN,
            "reminder_delay_days": "3",
            "location_text": "Ciudad Autónoma de Buenos Aires, Argentina",
            "objective": "1",
            "max_raw_records": "10",
            "overture_min_confidence": "0.750",
            "daily_limit": "30",
            "message_interval_minutes": "1",
            "weekdays": ["0", "1", "2", "3", "4", "5", "6"],
            "window_start": "00:01",
            "window_end": "23:59",
            "timezone_name": "America/Argentina/Buenos_Aires",
            "relevance_threshold": "70",
            "catalog": str(catalog.pk),
            "catalogs": [str(catalog.pk)],
            "categories": [str(category.pk)],
            "provinces": [str(zone.parent_id)],
            "zones": [str(zone.pk)],
        },
    )
    assert created.status_code == 302
    campaign = Campaign.objects.get(name="Aceptación solo revisión")

    with django_capture_on_commit_callbacks(execute=True):
        started = client.post(reverse("campaign-action", args=(campaign.pk, "start")), follow=True)

    assert started.status_code == 200
    campaign.refresh_from_db()
    assert campaign.state == Campaign.State.AWAITING_APPROVAL
    assert campaign.search_runs.exists()
    assert campaign.prospects.exists()

    approved = client.post(reverse("campaign-approve", args=(campaign.pk,)), follow=True)
    assert approved.status_code == 200

    message = campaign.messages.get(kind=OutboundMessage.Kind.INITIAL)
    assert message.state == OutboundMessage.State.REVIEW_READY
    assert message.message_id == ""
    assert deliver_outbound_messages() == 0
    detail = client.get(reverse("outbound-detail", args=(message.pk,)))
    assert detail.status_code == 200
    assert message.subject in detail.content.decode()
    assert message.body_text in detail.content.decode()
    assert "Este correo no fue enviado" in detail.content.decode()
    dashboard = client.get(reverse("dashboard"), {"campaign": campaign.pk})
    assert dashboard.context["metrics"]["review_ready"] == 1
    assert not GmailConnection.objects.filter(owner=owner).exists()
    assert not FakeGmailMessage.objects.exists()
    assert not ContactLedger.objects.exists()


@pytest.mark.e2e
@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_review_only_local_overture_flow_never_uses_network_mime_or_gmail_send(
    client: Client,
    owner: User,
    private_catalog_dir: object,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    del private_catalog_dir
    assert client.login(username=owner.username, password="correct-password")

    configured = client.post(
        reverse("integrations"),
        {
            "extractor_provider": "overture",
            "overture_min_confidence": "0.750",
            "website_fetcher": "fake",
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

    connected = client.post(reverse("gmail-connect"), follow=True)
    assert connected.status_code == 200
    assert GmailConnection.objects.filter(owner=owner).exists()
    assert not FakeGmailMessage.objects.exists()

    unexpected_external_calls: list[str] = []

    def reject_external_call(label: str):  # type: ignore[no-untyped-def]
        def reject(*args: object, **kwargs: object) -> None:
            del args, kwargs
            unexpected_external_calls.append(label)
            raise AssertionError(f"El flujo review-only intentó usar {label}.")

        return reject

    monkeypatch.setattr(
        OfficialOverturePlaceReader,
        "iter_places",
        reject_external_call("el lector remoto de Overture"),
    )
    monkeypatch.setattr(
        HttpWebsiteFetcher,
        "fetch",
        reject_external_call("el fetcher HTTP"),
    )
    monkeypatch.setattr(
        OllamaProvider,
        "analyze",
        reject_external_call("Ollama por HTTP"),
    )
    monkeypatch.setattr(
        OpenAICompatibleProvider,
        "analyze",
        reject_external_call("el LLM compatible por HTTP"),
    )

    mx_queries: list[str] = []

    class OfflineMXResolver:
        def resolve(self, domain: str) -> MXStatus:
            mx_queries.append(domain)
            return MXStatus.VALID

    monkeypatch.setattr("apps.campaigns.extraction.DNSMXResolver", OfflineMXResolver)

    gmail_send_calls: list[str] = []

    def reject_gmail_send(*args: object, **kwargs: object) -> None:
        del args, kwargs
        gmail_send_calls.append("send")
        raise AssertionError("Una campaña review-only intentó enviar por Gmail.")

    monkeypatch.setattr(FakeGmailProvider, "send", reject_gmail_send)

    mime_calls: list[str] = []

    def reject_mime(*args: object, **kwargs: object) -> None:
        del args, kwargs
        mime_calls.append("mime")
        raise AssertionError("Una campaña review-only intentó construir MIME.")

    monkeypatch.setattr("apps.campaigns.delivery._message_bytes", reject_mime)

    assert (
        client.post(
            reverse("business-profile"),
            {
                "company_name": "Carbones Overture",
                "salesperson_name": "Fran",
                "phone": "",
                "whatsapp": "",
                "description": "Proveedor industrial",
                "products": "Carbones para motores",
                "differentiators": "Atención directa",
                "address": "CABA",
                "website": "",
                "signature": "Fran · Carbones Overture",
                "additional_instructions": "",
                "relevance_threshold": "70",
            },
        ).status_code
        == 302
    )
    assert (
        client.post(
            reverse("catalogs"),
            {
                "name": "Catálogo Overture local",
                "file": SimpleUploadedFile(
                    "catalogo.pdf",
                    b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
                    content_type="application/pdf",
                ),
            },
        ).status_code
        == 302
    )

    category = SearchCategory.objects.get(name="Bobinados de motores")
    zone = SearchZone.objects.get(name="Palermo")
    province = zone.parent
    assert province is not None
    boundary_manifest = hashlib.sha256(
        json.dumps(
            [{"code": "palermo", "boundary_hash": zone.boundary_hash}],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    release = OvertureRelease.objects.create(
        release_id="2026-07-22.0",
        schema_version="v1.18.0",
        taxonomy_version="2026-07-22.0",
        importer_version="overture-importer-v1",
        mapping_version="category-map-v1",
        source_uri=official_places_source_uri("2026-07-22.0"),
        catalog_url="https://stac.overturemaps.org/2026-07-22.0/catalog.json",
        manifest_sha256="a" * 64,
        metadata_sha256="b" * 64,
    )
    snapshot = OvertureDatasetSnapshot.objects.create(
        release_id="2026-07-22.0",
        release_record=release,
        province_code=province.official_code,
        province_name=province.name,
        province_bbox=province.boundary_bbox,
        schema_version="v1.18.0",
        taxonomy_version="2026-07-22.0",
        importer_version="overture-importer-v1",
        mapping_version="category-map-v1",
        boundary_version="caba-seed-v1",
        boundary_manifest_sha256=boundary_manifest,
        source_uri=official_places_source_uri("2026-07-22.0"),
        manifest_sha256="a" * 64,
        status=OvertureDatasetSnapshot.Status.IMPORTING,
        is_active=False,
        streamed_count=1,
        place_count=1,
        zone_count=1,
        taxonomy_code_count=0,
        source_counts={"Overture": 1},
        source_licenses=[],
        attribution=OVERTURE_ATTRIBUTION,
        notices=[],
        validation_results={"passed": True, "place_limit": 500_000},
    )
    partition = OvertureCoveragePartition.objects.create(
        release=release,
        province=province,
        province_code=province.official_code,
        province_name=province.name,
        province_bbox=province.boundary_bbox,
        snapshot=snapshot,
        status=OvertureCoveragePartition.Status.IMPORTING,
        boundary_version=snapshot.boundary_version,
        boundary_manifest_sha256=snapshot.boundary_manifest_sha256,
        zone_count=1,
    )
    dataset_zone = OvertureDatasetZone.objects.create(
        snapshot=snapshot,
        partition=partition,
        search_zone=zone,
        code="palermo",
        name=zone.name,
        normalized_name=zone.normalized_name,
        geometry=zone.boundary_geojson,
        bbox=zone.boundary_bbox,
        boundary_hash=zone.boundary_hash,
        source=zone.boundary_source,
        source_version=str(zone.boundary_revision),
        attribution=zone.boundary_attribution,
    )
    overture_id = "08f2a100-6f6a-4f31-9a6f-2e931c237f81"
    place = OverturePlace.objects.create(
        snapshot=snapshot,
        partition=partition,
        overture_id=overture_id,
        name="Bobinados Overture Palermo",
        normalized_name="bobinados overture palermo",
        name_search=padded_name_search("Bobinados Overture Palermo"),
        names={"primary": "Bobinados Overture Palermo"},
        address="Palermo, Ciudad Autónoma de Buenos Aires",
        address_data={"locality": "Ciudad Autónoma de Buenos Aires"},
        websites=[{"url": "https://bobinados-overture.example"}],
        emails=[
            {
                "value": "ventas@bobinados-overture.example",
                "source": "overture",
                "is_primary": True,
            }
        ],
        phones=[{"value": "+54 11 5555 0101"}],
        primary_category="services_and_business",
        basic_category="services_and_business",
        taxonomy={"primary": "services_and_business"},
        taxonomy_codes=["services_and_business"],
        taxonomy_codes_search=taxonomy_codes_search(["services_and_business"]),
        latitude=Decimal("-34.5800000"),
        longitude=Decimal("-58.4200000"),
        confidence=Decimal("0.9200"),
        operating_status="open",
        sources=[{"dataset": "Overture", "record_id": overture_id}],
        field_provenance={"/": [{"dataset": "Overture"}]},
        source_licenses=[],
        license="",
        source_payload_hash="c" * 64,
    )
    OverturePlaceZone.objects.create(
        snapshot=snapshot, partition=partition, place=place, zone=dataset_zone
    )
    snapshot.status = OvertureDatasetSnapshot.Status.READY
    snapshot.is_active = True
    snapshot.save(update_fields=("status", "is_active", "updated_at"))
    partition.status = OvertureCoveragePartition.Status.READY
    partition.is_active = True
    partition.save(update_fields=("status", "is_active", "updated_at"))

    catalog = owner.catalogs.get()
    created = client.post(
        reverse("campaign-create"),
        {
            "name": "Aceptación Overture local",
            "delivery_mode": Campaign.DeliveryMode.REVIEW_ONLY,
            "approval_mode": Campaign.ApprovalMode.CAMPAIGN,
            "reminder_delay_days": "3",
            "location_text": "Ciudad Autónoma de Buenos Aires, Argentina",
            "objective": "1",
            "max_raw_records": "10",
            "overture_min_confidence": "0.750",
            "daily_limit": "30",
            "message_interval_minutes": "1",
            "weekdays": ["0", "1", "2", "3", "4", "5", "6"],
            "window_start": "00:01",
            "window_end": "23:59",
            "timezone_name": "America/Argentina/Buenos_Aires",
            "relevance_threshold": "70",
            "catalog": str(catalog.pk),
            "catalogs": [str(catalog.pk)],
            "categories": [str(category.pk)],
            "provinces": [str(zone.parent_id)],
            "zones": [str(zone.pk)],
        },
    )
    assert created.status_code == 302
    campaign = Campaign.objects.get(name="Aceptación Overture local")

    with django_capture_on_commit_callbacks(execute=True):
        started = client.post(reverse("campaign-action", args=(campaign.pk, "start")), follow=True)

    assert started.status_code == 200
    campaign.refresh_from_db()
    assert campaign.state == Campaign.State.AWAITING_APPROVAL
    run = campaign.search_runs.get()
    prospect = campaign.prospects.get()
    email = prospect.emails.get(is_primary=True)
    website_snapshot = WebsiteSnapshot.objects.get(prospect=prospect)

    approved = client.post(reverse("campaign-approve", args=(campaign.pk,)), follow=True)
    assert approved.status_code == 200

    message = campaign.messages.get(kind=OutboundMessage.Kind.INITIAL)
    assert campaign.overture_snapshot_id == snapshot.pk
    assert run.overture_snapshot_id == snapshot.pk
    assert run.provider == "overture"
    assert run.usage.operation == "overture_places_query"
    assert run.usage.actual_cost == Decimal("0")
    assert prospect.pipeline_state == Prospect.PipelineState.QUEUED
    assert prospect.provider_data["overture_id"] == overture_id
    assert email.normalized_email == "ventas@bobinados-overture.example"
    assert email.source == "overture"
    assert website_snapshot.status == WebsiteSnapshot.Status.SUCCESS
    assert message.state == OutboundMessage.State.REVIEW_READY
    assert message.delivery_mode == Campaign.DeliveryMode.REVIEW_ONLY
    assert message.message_id == ""
    assert message.mime_sha256 == ""
    assert deliver_outbound_messages() == 0

    detail = client.get(reverse("outbound-detail", args=(message.pk,)))
    assert detail.status_code == 200
    assert message.subject in detail.content.decode()
    assert message.body_text in detail.content.decode()
    assert "Este correo no fue enviado" in detail.content.decode()

    assert mx_queries == ["bobinados-overture.example"]
    assert unexpected_external_calls == []
    assert gmail_send_calls == []
    assert mime_calls == []
    assert not FakeGmailMessage.objects.exists()
    assert not ContactLedger.objects.exists()


@pytest.mark.e2e
@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_full_fake_acceptance_flow_from_ui(
    client: Client,
    owner: User,
    private_catalog_dir: object,
    django_capture_on_commit_callbacks: Any,
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
            "approval_mode": Campaign.ApprovalMode.PER_MESSAGE,
            "reminder_delay_days": "3",
            "confirm_live": "on",
            "location_text": "Ciudad Autónoma de Buenos Aires, Argentina",
            "objective": "1",
            "max_raw_records": "10",
            "overture_min_confidence": "0.750",
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
            "catalogs": [str(catalog.pk)],
            "categories": [str(category.pk)],
            "provinces": [str(zone.parent_id)],
            "zones": [str(zone.pk)],
        },
    )
    assert created.status_code == 302
    campaign = Campaign.objects.get(name="Aceptación fake UI")
    with django_capture_on_commit_callbacks(execute=True):
        started = client.post(reverse("campaign-action", args=(campaign.pk, "start")), follow=True)
    assert started.status_code == 200
    assert campaign.prospects.filter(pipeline_state="QUEUED").exists()
    message = campaign.messages.get(kind=OutboundMessage.Kind.INITIAL)
    assert message.state == OutboundMessage.State.REVIEW_READY
    assert message.approved_at is None
    detail = client.get(reverse("outbound-detail", args=(message.pk,)))
    assert detail.status_code == 200
    assert "esperando edición y aprobación explícita" in detail.content.decode()
    edited = client.post(
        reverse("outbound-edit", args=(message.pk,)),
        {"subject": "Consulta para coordinar una visita", "body_text": message.body_text},
        follow=True,
    )
    assert edited.status_code == 200
    message.refresh_from_db()
    assert message.subject == "Consulta para coordinar una visita"
    assert message.content_revision == 2

    approved = client.post(reverse("outbound-approve", args=(message.pk,)), follow=True)
    assert approved.status_code == 200
    assert "Se enviará cuando la campaña vuelva a estar en curso" in approved.content.decode()

    started_approved = client.post(
        reverse("campaign-start-approved", args=(campaign.pk,)), follow=True
    )
    assert started_approved.status_code == 200

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
