from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.campaigns.models import Campaign, OutboundMessage
from apps.compliance.models import ContactLedger
from apps.configuration.models import IntegrationConfiguration, SearchCategory, SearchZone
from apps.integrations.contracts import SearchRequest
from apps.integrations.factory import get_extractor_provider
from apps.integrations.fakes import FakeGmailProvider
from apps.integrations.llm import OllamaProvider, OpenAICompatibleProvider
from apps.integrations.website import HttpWebsiteFetcher
from apps.mailbox.models import FakeGmailMessage, GmailConnection
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

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"
PASSWORD = "correct-password"


class Operator:
    """A signed-in administrator driving the application only through ``/api/v1``.

    These tests used to post to the server-rendered pages; the browser now talks to the API, so
    that is the surface that has to work end to end. CSRF is enforced exactly as in production.
    """

    def __init__(self, username: str) -> None:
        self.client = Client(enforce_csrf_checks=True)
        self.csrf = self._csrf()
        response = self.post(
            reverse("api-auth-login"), {"username": username, "password": PASSWORD}
        )
        assert response.status_code == 200, response.content
        self.csrf = self._csrf()

    def _csrf(self) -> str:
        return str(self.client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])

    def get(self, url: str, params: dict[str, Any] | None = None):
        return self.client.get(url, params or {})

    def post(self, url: str, payload: dict[str, Any] | None = None, **headers: str):
        return self.client.post(
            url,
            data=json.dumps(payload or {}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.csrf,
            **headers,
        )

    def patch(self, url: str, payload: dict[str, Any]):
        return self.client.patch(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.csrf,
        )

    def save_profile(self, company: str) -> None:
        response = self.patch(
            reverse("api-workspace-profile"),
            {
                "company_name": company,
                "salesperson_name": "Vendedor",
                "phone": "",
                "whatsapp": "",
                "description": "Proveedor industrial",
                "products": "Componentes industriales",
                "differentiators": "Atención directa",
                "address": "CABA",
                "website": "",
                "signature": f"Vendedor · {company}",
                "additional_instructions": "",
            },
        )
        assert response.status_code == 200, response.content

    def upload_catalog(self, name: str) -> str:
        response = self.client.post(
            reverse("api-catalogs"),
            {
                "name": name,
                "file": SimpleUploadedFile("catalogo.pdf", PDF, content_type="application/pdf"),
            },
            HTTP_X_CSRFTOKEN=self.csrf,
        )
        assert response.status_code == 201, response.content
        return str(response.json()["data"]["id"])

    def create_review_only_campaign(self, name: str, catalog_id: str) -> str:
        category = SearchCategory.objects.get(name="Bobinados de motores")
        zone = SearchZone.objects.get(name="Palermo")
        response = self.post(
            reverse("api-campaigns"),
            {
                "name": name,
                "delivery_mode": Campaign.DeliveryMode.REVIEW_ONLY,
                "approval_mode": Campaign.ApprovalMode.CAMPAIGN,
                "reminder_delay_days": 3,
                "location_text": "Ciudad Autónoma de Buenos Aires, Argentina",
                "objective": 1,
                "max_raw_records": 10,
                "overture_min_confidence": "0.750",
                "daily_limit": 30,
                "message_interval_minutes": 1,
                "weekdays": [0, 1, 2, 3, 4, 5, 6],
                "window_start": "00:01",
                "window_end": "23:59",
                "timezone_name": "America/Argentina/Buenos_Aires",
                "catalogs": [catalog_id],
                "categories": [str(category.pk)],
                "provinces": [str(zone.parent_id)],
                "zones": [str(zone.pk)],
            },
        )
        assert response.status_code == 201, response.content
        return str(response.json()["data"]["id"])

    def run_action(self, campaign_id: str, action: str):
        return self.post(
            reverse("api-campaign-action", args=(campaign_id, action)),
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

    def connect_gmail(self) -> None:
        """Run the whole OAuth round trip against the fake provider: start, then the callback."""
        started = self.post(reverse("api-gmail-oauth-start"))
        assert started.status_code == 200, started.content
        query = parse_qs(urlsplit(started.json()["data"]["authorization_url"]).query)
        callback = self.get(
            reverse("api-gmail-oauth-callback"),
            {"state": query["state"][0], "code": query["code"][0]},
        )
        assert callback.status_code == 302
        assert callback["Location"].endswith("gmail=connected"), callback["Location"]


def _use_overture_extractor(owner: User) -> None:
    """Switch the workspace to the local Overture extractor.

    Provider selection is deployment configuration, not something the API edits, so the test sets
    the stored configuration directly.
    """
    IntegrationConfiguration.objects.update_or_create(
        workspace=owner.membership.workspace,
        defaults={"owner": owner, "extractor_provider": "overture"},
    )


@pytest.mark.e2e
@pytest.mark.django_db
def test_owner_signs_in_and_the_fake_providers_and_worker_run(owner: User) -> None:
    operator = Operator(owner.username)

    assert operator.get(reverse("api-dashboard-summary")).status_code == 200
    status = operator.get(reverse("api-integrations-status")).json()["data"]
    assert status["extractor"]["provider"] == "fake"
    assert status["llm"]["provider"] == "fake"
    assert status["gmail"]["provider"] == "fake"
    batch = get_extractor_provider(owner_id=owner.pk).search(
        SearchRequest(query="demo", correlation_id="e2e", idempotency_key="e2e")
    )
    assert batch.records
    assert healthcheck.apply().get()["status"] == "ok"


@pytest.mark.e2e
@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_review_only_fake_flow_searches_drafts_and_exposes_content_without_gmail(
    owner: User,
    private_catalog_dir: object,
    django_capture_on_commit_callbacks: Any,
) -> None:
    del private_catalog_dir
    operator = Operator(owner.username)
    operator.save_profile("Componentes Revisión")
    campaign_id = operator.create_review_only_campaign(
        "Aceptación solo revisión", operator.upload_catalog("Catálogo revisión")
    )

    with django_capture_on_commit_callbacks(execute=True):
        started = operator.run_action(campaign_id, "start-discovery")
    assert started.status_code == 200, started.content

    campaign = Campaign.objects.get(pk=campaign_id)
    assert campaign.state == Campaign.State.AWAITING_APPROVAL
    assert campaign.search_runs.exists()
    assert campaign.prospects.exists()

    with django_capture_on_commit_callbacks(execute=True):
        approved = operator.run_action(campaign_id, "approve")
    assert approved.status_code == 200, approved.content

    message = campaign.messages.get(kind=OutboundMessage.Kind.INITIAL)
    assert message.state == OutboundMessage.State.REVIEW_READY
    assert message.message_id == ""
    assert deliver_outbound_messages() == 0

    detail = operator.get(reverse("api-outbound-message-detail", args=(message.pk,))).json()["data"]
    assert detail["subject"] == message.subject
    assert detail["body_text"] == message.body_text
    assert detail["state"] == OutboundMessage.State.REVIEW_READY
    assert detail["sent_at"] is None and detail["message_id"] is None
    campaign_view = operator.get(reverse("api-campaign-detail", args=(campaign_id,))).json()["data"]
    assert campaign_view["metrics"]["review_ready"] == 1
    assert campaign_view["metrics"]["sent"] == 0
    assert not GmailConnection.objects.filter(owner=owner).exists()
    assert not FakeGmailMessage.objects.exists()
    assert not ContactLedger.objects.exists()


@pytest.mark.e2e
@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_review_only_local_overture_flow_never_uses_network_mime_or_gmail_send(
    owner: User,
    private_catalog_dir: object,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    del private_catalog_dir
    operator = Operator(owner.username)
    _use_overture_extractor(owner)

    operator.connect_gmail()
    connection = operator.get(reverse("api-gmail-connection")).json()["data"]
    assert connection["connected"] is True
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
    monkeypatch.setattr(HttpWebsiteFetcher, "fetch", reject_external_call("el fetcher HTTP"))
    monkeypatch.setattr(OllamaProvider, "analyze", reject_external_call("Ollama por HTTP"))
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

    operator.save_profile("Componentes Overture")
    catalog_id = operator.upload_catalog("Catálogo Overture local")

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

    campaign_id = operator.create_review_only_campaign("Aceptación Overture local", catalog_id)
    campaign = Campaign.objects.get(pk=campaign_id)

    with django_capture_on_commit_callbacks(execute=True):
        started = operator.run_action(campaign_id, "start-discovery")
    assert started.status_code == 200, started.content

    campaign.refresh_from_db()
    assert campaign.state == Campaign.State.AWAITING_APPROVAL
    run = campaign.search_runs.get()
    prospect = campaign.prospects.get()
    email = prospect.emails.get(is_primary=True)
    website_snapshot = WebsiteSnapshot.objects.get(prospect=prospect)

    with django_capture_on_commit_callbacks(execute=True):
        approved = operator.run_action(campaign_id, "approve")
    assert approved.status_code == 200, approved.content

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

    detail = operator.get(reverse("api-outbound-message-detail", args=(message.pk,))).json()["data"]
    assert detail["subject"] == message.subject
    assert detail["body_text"] == message.body_text
    assert detail["state"] == OutboundMessage.State.REVIEW_READY
    assert detail["sent_at"] is None

    # Overture's attribution stays visible to the team, on the coverage status and on the record.
    coverage = operator.get(reverse("api-overture-status")).json()["data"]
    assert coverage["attribution"]["attribution"] == OVERTURE_ATTRIBUTION
    assert coverage["attribution"]["release_id"] == "2026-07-22.0"
    listed = operator.get(reverse("api-prospects")).json()["data"]
    provenance = next(item for item in listed if item["id"] == str(prospect.pk))["provenance"]
    assert provenance["overture_id"] == overture_id
    assert provenance["release_id"] == "2026-07-22.0"
    assert OVERTURE_ATTRIBUTION in provenance["attribution"]
    assert provenance["contact_source"]["source"] == "overture"

    assert mx_queries == ["bobinados-overture.example"]
    assert unexpected_external_calls == []
    assert gmail_send_calls == []
    assert mime_calls == []
    assert not FakeGmailMessage.objects.exists()
    assert not ContactLedger.objects.exists()
