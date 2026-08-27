from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent, BackgroundJob
from apps.audit.observability import RedactingJsonFormatter, correlation_id_var
from apps.audit.services import record_event
from apps.automation.models import HumanTask, ReplyDecision
from apps.campaigns.models import Campaign, OutboundMessage, SearchQuery, SearchRun
from apps.catalogs.services import create_catalog
from apps.contacts.models import Contact, Conversation, EmailAddress, Organization
from apps.dashboard.csv_export import spreadsheet_safe
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import GmailConnection, InboundMessage
from apps.overture.models import OvertureDatasetSnapshot
from apps.prospects.models import AIAnalysis, Prospect, ProspectEmail, WebsiteSnapshot


def _operational_data(owner: User) -> tuple[Campaign, Prospect, OutboundMessage, InboundMessage]:
    catalog = create_catalog(
        name="Operación",
        upload=SimpleUploadedFile(
            "catalogo.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
            content_type="application/pdf",
        ),
        actor=owner,
    )
    campaign = Campaign.objects.create(
        name="Campaña observable",
        state=Campaign.State.PAUSED,
        discovery_state=Campaign.DiscoveryState.EXHAUSTED_QUERIES,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        catalog=catalog,
        created_by=owner,
    )
    query = SearchQuery.objects.create(
        campaign=campaign,
        category_snapshot="Taller",
        zone_snapshot="Palermo",
        location_snapshot="CABA",
        query_text="Taller Palermo",
        normalized_query="taller palermo",
    )
    run = SearchRun.objects.create(
        campaign=campaign,
        query=query,
        provider="fake",
        idempotency_key=f"run:{campaign.pk}",
        requested_limit=10,
        raw_count=3,
        email_count=1,
        duplicate_count=1,
        state=SearchRun.State.SUCCEEDED,
    )
    prospect = Prospect.objects.create(
        campaign=campaign,
        source_run=run,
        name="=Taller Fórmula",
        normalized_name="taller formula",
        category="Motores",
        neighborhood="Palermo",
        pipeline_state=Prospect.PipelineState.QUEUED,
    )
    email = ProspectEmail.objects.create(
        prospect=prospect,
        original_email="ventas@example.com",
        normalized_email="ventas@example.com",
        domain="example.com",
        local_part="ventas",
        source="fixture",
        provider_order=10,
        mx_status=ProspectEmail.MXStatus.VALID,
        mx_checked_at=timezone.now(),
        is_primary=True,
    )
    analysis = AIAnalysis.objects.create(
        prospect=prospect,
        input_hash="a" * 64,
        prompt_version="v1",
        schema_version="v1",
        provider="fake",
        model="fake",
        analyzed_at=timezone.now(),
        status=AIAnalysis.Status.VALID,
        relevance_score=91,
        confidence="0.900",
        relevance_reason="Actividad compatible",
        prompt_text="fixture",
    )
    outbound = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.FIRST_CONTACT,
        campaign=campaign,
        prospect=prospect,
        prospect_email=email,
        analysis=analysis,
        recipient="ventas@example.com",
        recipient_normalized="ventas@example.com",
        subject="PUBLICIDAD - Consulta",
        body_text="Texto de prueba con BAJA.",
        catalog=catalog,
        catalog_version=catalog.version,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key=f"message:{prospect.pk}",
        message_id=f"<message-{prospect.pk}@example.invalid>",
        gmail_thread_id="fake-thread",
        state=OutboundMessage.State.SEND_FAILED,
        error="Proveedor corregible",
    )
    connection = GmailConnection.objects.create(
        owner=owner,
        email="owner@example.invalid",
        scopes=[],
        refresh_token_encrypted=encrypt_token("refresh"),
        status=GmailConnection.Status.CONNECTED,
    )
    inbound = InboundMessage.objects.create(
        connection=connection,
        related_outbound=outbound,
        gmail_message_id="inbound-one",
        gmail_thread_id="fake-thread",
        message_id="<inbound@example.invalid>",
        in_reply_to=outbound.message_id,
        sender="cliente@example.com",
        recipients=[connection.email],
        subject="Re: Consulta",
        external_at=datetime(2026, 7, 16, 12, tzinfo=UTC),
        received_at=timezone.now(),
        body_text="Me interesa",
        classification=InboundMessage.Classification.INTERESTED,
        classification_confidence="0.900",
    )
    return campaign, prospect, outbound, inbound


def _reviewable_body() -> str:
    return (
        "Te contacto porque trabajamos con carbones para motores eléctricos y queremos "
        "conversar sobre una posible aplicación en la actividad del negocio. Contamos con "
        "distintas medidas y alternativas para tareas de reparación y mantenimiento, sin "
        "asumir qué modelos utilizan actualmente. La idea es que nuestro vendedor pueda "
        "acercarse, conocer la necesidad concreta y mostrar el catálogo técnico disponible. "
        "¿Qué día conviene que pase el vendedor?\n\n"
        "Fran · Carbones SA\nCarbones SA · CABA"
    )


@pytest.mark.django_db
def test_operational_views_filter_paginate_and_export_safely(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign, prospect, outbound, inbound = _operational_data(owner)
    ProspectEmail.objects.create(
        prospect=prospect,
        original_email="contacto@example.net",
        normalized_email="contacto@example.net",
        domain="example.net",
        local_part="contacto",
        source="fixture-secondary",
        provider_order=0,
        mx_status=ProspectEmail.MXStatus.VALID,
        mx_checked_at=timezone.now(),
        is_primary=False,
    )
    OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.MANUAL_REPLY,
        campaign=campaign,
        prospect=prospect,
        prospect_email=outbound.prospect_email,
        parent_inbound=inbound,
        sent_by=owner,
        recipient=outbound.recipient,
        recipient_normalized=outbound.recipient_normalized,
        subject="Re: Consulta",
        body_text="Respuesta manual",
        catalog=outbound.catalog,
        catalog_version=outbound.catalog_version,
        state=OutboundMessage.State.SENT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key=f"manual:{inbound.pk}",
        message_id=f"<manual-{inbound.pk}@example.invalid>",
    )
    record_event(action="campaign.checked", entity=campaign, actor=owner)
    organization = Organization.objects.create(
        workspace=campaign.workspace,
        name="Cliente operativo",
        normalized_name="cliente operativo",
    )
    contact_email = EmailAddress.objects.create(
        workspace=campaign.workspace,
        organization=organization,
        original_email="cliente-operativo@example.com",
        normalized_email="cliente-operativo@example.com",
        domain="example.com",
        validity=EmailAddress.Validity.VALID,
        is_preferred=True,
    )
    contact = Contact.objects.create(
        workspace=campaign.workspace,
        organization=organization,
        preferred_email=contact_email,
        name="Cliente operativo",
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
        created_by=owner,
    )
    conversation = Conversation.objects.create(
        workspace=campaign.workspace,
        contact=contact,
        connection=inbound.connection,
        gmail_thread_id="dashboard-review-thread",
        subject="Consulta por envíos",
    )
    inbound.organization = organization
    inbound.contact = contact
    inbound.conversation = conversation
    inbound.save(update_fields=("organization", "contact", "conversation", "updated_at"))
    decision = ReplyDecision.objects.create(
        workspace=campaign.workspace,
        inbound=inbound,
        contact=contact,
        conversation=conversation,
        mode="LIVE",
        provider="fake",
        model="fake",
        policy_version="reply-policy-v1",
        classification="INTERESTED",
        intent="APPROVED_PRODUCT_INFORMATION",
        action="REPLY",
        confidence="0.900",
        human_reason="HUMAN_TASK_OPEN",
        context_manifest={},
        context_hash="b" * 64,
        state=ReplyDecision.State.REJECTED_POLICY,
        error="Hay una revisión humana pendiente para este contacto.",
    )
    HumanTask.objects.create(
        workspace=campaign.workspace,
        contact=contact,
        conversation=conversation,
        inbound=inbound,
        decision=decision,
        kind="REPLY_REVIEW",
        reason="HUMAN_TASK_OPEN",
        status=HumanTask.Status.OPEN,
        friendly_summary="Hay una revisión humana pendiente para este contacto.",
        opened_at=timezone.now(),
    )
    client.force_login(owner)

    dashboard = client.get(reverse("dashboard"), {"campaign": campaign.pk})
    assert dashboard.status_code == 200
    assert dashboard.context["metrics"]["raw"] == 3
    assert dashboard.context["metrics"]["interested"] == 1
    assert dashboard.context["metrics"]["sent"] == 0
    assert dashboard.context["metrics"]["failed"] == 1
    dashboard_page = dashboard.content.decode()
    assert "Ya hay una revisión abierta para este contacto" in dashboard_page
    assert "Hay una revisión humana pendiente para este contacto." in dashboard_page
    assert client.get(reverse("dashboard"), {"campaign": "inválida"}).status_code == 200

    prospects = client.get(
        reverse("prospects"),
        {"q": "Fórmula", "campaign": campaign.pk, "min_score": "90"},
    )
    assert list(prospects.context["page_obj"]) == [prospect]
    assert "Actividad compatible" in prospects.content.decode()
    assert "ventas@example.com" in prospects.content.decode()
    assert "contacto@example.net" not in prospects.content.decode()
    assert client.get(reverse("prospects"), {"min_score": "no-numérico"}).status_code == 200
    assert client.get(reverse("prospects"), {"campaign": "inválida"}).status_code == 200
    combined = client.get(
        reverse("prospects"),
        {
            "state": "QUEUED",
            "category": "motor",
            "neighborhood": "paler",
            "max_score": "95",
            "date_from": "2020-01-01",
            "date_to": "2030-01-01",
        },
    )
    assert list(combined.context["page_obj"]) == [prospect]
    assert client.get(reverse("prospects"), {"max_score": "x"}).status_code == 200

    sent = client.get(
        reverse("outbound-messages"),
        {
            "q": "Consulta",
            "campaign": campaign.pk,
            "state": "SEND_FAILED",
            "kind": "FIRST_CONTACT",
            "date_from": "2020-01-01",
            "date_to": "2030-01-01",
        },
    )
    assert list(sent.context["page_obj"]) == [outbound]
    assert "Texto de prueba con BAJA." not in sent.content.decode()
    detail = client.get(reverse("outbound-detail", args=(outbound.pk,)))
    assert detail.status_code == 200
    assert "Texto de prueba con BAJA." in detail.content.decode()
    assert "no-store" in detail.headers["Cache-Control"]
    OutboundMessage.objects.filter(pk=outbound.pk).update(
        body_text="<script>alert('no ejecutar')</script>"
    )
    escaped_detail = client.get(reverse("outbound-detail", args=(outbound.pk,)))
    assert "&lt;script&gt;" in escaped_detail.content.decode()
    assert "<script>alert('no ejecutar')</script>" not in escaped_detail.content.decode()
    other = User.objects.create_user(username="other-owner", password="other-password")
    client.force_login(other)
    assert client.get(reverse("outbound-detail", args=(outbound.pk,))).status_code == 404
    client.force_login(owner)
    responses = client.get(
        reverse("responses"),
        {
            "q": "cliente",
            "campaign": campaign.pk,
            "classification": "INTERESTED",
            "interest": "human",
            "date_from": "2020-01-01",
            "date_to": "2030-01-01",
        },
    )
    assert list(responses.context["page_obj"]) == [inbound]
    assert client.get(reverse("responses"), {"interest": "interested"}).status_code == 200
    assert (
        client.get(
            reverse("audit-log"),
            {"q": "checked", "action": "campaign", "entity": "Campaign"},
        ).status_code
        == 200
    )
    assert (
        client.get(reverse("campaigns"), {"q": "observable", "state": "PAUSED"}).status_code == 200
    )

    csv_response = client.get(reverse("prospects-export"), {"campaign": campaign.pk})
    assert csv_response.status_code == 200
    assert "'=Taller Fórmula" in csv_response.content.decode("utf-8-sig")
    assert "ventas@example.com" in csv_response.content.decode("utf-8-sig")
    assert "contacto@example.net" not in csv_response.content.decode("utf-8-sig")
    assert client.get(reverse("outbound-export")).status_code == 200
    assert client.get(reverse("responses-export")).status_code == 200
    assert spreadsheet_safe("+SUM(1,1)") == "'+SUM(1,1)"
    assert spreadsheet_safe("normal") == "normal"


@pytest.mark.django_db
def test_review_ready_email_is_counted_and_labeled_as_not_sent(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign, _, outbound, _ = _operational_data(owner)
    Campaign.objects.filter(pk=campaign.pk).update(delivery_mode=Campaign.DeliveryMode.REVIEW_ONLY)
    OutboundMessage.objects.filter(pk=outbound.pk).update(
        delivery_mode=Campaign.DeliveryMode.REVIEW_ONLY,
        state=OutboundMessage.State.REVIEW_READY,
        error="",
    )
    client.force_login(owner)

    dashboard = client.get(reverse("dashboard"), {"campaign": campaign.pk})
    listing = client.get(
        reverse("outbound-messages"),
        {"campaign": campaign.pk, "state": OutboundMessage.State.REVIEW_READY},
    )
    detail = client.get(reverse("outbound-detail", args=(outbound.pk,)))

    assert dashboard.context["metrics"]["review_ready"] == 1
    assert dashboard.context["metrics"]["queued"] == 0
    assert list(listing.context["page_obj"]) == [outbound]
    assert "Listo para revisar" in listing.content.decode()
    assert "Este correo no fue enviado" in detail.content.decode()
    assert "no-store" in detail.headers["Cache-Control"]


@pytest.mark.django_db
def test_live_draft_can_be_edited_then_requires_audited_approval(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign, _, outbound, _ = _operational_data(owner)
    Campaign.objects.filter(pk=campaign.pk).update(
        profile_snapshot={
            "company_name": "Carbones SA",
            "salesperson_name": "Fran",
            "address": "CABA",
            "signature": "Fran · Carbones SA",
        }
    )
    OutboundMessage.objects.filter(pk=outbound.pk).update(
        subject="Consulta técnica",
        body_text=_reviewable_body(),
        state=OutboundMessage.State.REVIEW_READY,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        approved_at=None,
        approved_by=None,
        error="",
    )
    client.force_login(owner)

    detail = client.get(reverse("outbound-detail", args=(outbound.pk,)))
    assert detail.status_code == 200
    assert detail.context["can_edit"] is True
    assert detail.context["can_approve"] is True
    assert "Aprobar para enviar" in detail.content.decode()

    invalid = client.post(
        reverse("outbound-edit", args=(outbound.pk,)),
        {"subject": "Cambio inválido", "body_text": "Sin firma"},
    )
    assert invalid.status_code == 400
    outbound.refresh_from_db()
    assert outbound.subject == "Consulta técnica"

    edited_body = _reviewable_body().replace("queremos conversar", "preferimos conversar")
    edited = client.post(
        reverse("outbound-edit", args=(outbound.pk,)),
        {"subject": "Consulta para coordinar", "body_text": edited_body},
        follow=True,
    )
    assert edited.status_code == 200
    outbound.refresh_from_db()
    assert outbound.subject == "Consulta para coordinar"
    assert outbound.body_text == edited_body
    assert outbound.content_revision == 2
    assert outbound.last_edited_by == owner
    assert outbound.state == OutboundMessage.State.REVIEW_READY
    assert outbound.approved_at is None

    approved = client.post(reverse("outbound-approve", args=(outbound.pk,)), follow=True)
    assert approved.status_code == 200
    outbound.refresh_from_db()
    assert outbound.state == OutboundMessage.State.QUEUED
    assert outbound.approved_by == owner
    assert outbound.approved_at is not None
    assert outbound.next_attempt_at is not None
    assert AuditEvent.objects.filter(
        entity_id=str(outbound.pk), action="message.draft_edited", actor=owner
    ).exists()
    approval_event = AuditEvent.objects.get(
        entity_id=str(outbound.pk), action="message.approved_for_delivery", actor=owner
    )
    assert approval_event.after["content_revision"] == 2
    assert edited_body not in json.dumps(approval_event.after)

    assert client.post(reverse("outbound-edit", args=(outbound.pk,)), {}).status_code == 400
    assert client.post(reverse("outbound-approve", args=(outbound.pk,))).status_code == 302

    other = User.objects.create_user(username="review-other", password="password")
    client.force_login(other)
    assert client.post(reverse("outbound-edit", args=(outbound.pk,)), {}).status_code == 403
    assert client.post(reverse("outbound-approve", args=(outbound.pk,))).status_code == 403


@pytest.mark.django_db
def test_draft_editor_normalizes_browser_crlf_and_accepts_migrated_short_copy(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign, _, outbound, _ = _operational_data(owner)
    Campaign.objects.filter(pk=campaign.pk).update(
        profile_snapshot={
            "company_name": "Carbones SA",
            "salesperson_name": "Fran",
            "address": "CABA",
            "signature": "Fran · Carbones SA",
        }
    )
    migrated_body = _reviewable_body().replace(
        "Contamos con distintas medidas y alternativas para tareas de reparación y mantenimiento",
        "Presentamos opciones",
    )
    OutboundMessage.objects.filter(pk=outbound.pk).update(
        subject="Consulta técnica",
        body_text=migrated_body,
        state=OutboundMessage.State.REVIEW_READY,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        content_revision=2,
        approved_at=None,
        approved_by=None,
        error="",
    )
    client.force_login(owner)

    edited = client.post(
        reverse("outbound-edit", args=(outbound.pk,)),
        {
            "subject": "Consulta técnica actualizada",
            "body_text": migrated_body.replace("\n", "\r\n"),
        },
        follow=True,
    )

    assert edited.status_code == 200
    outbound.refresh_from_db()
    assert outbound.subject == "Consulta técnica actualizada"
    assert outbound.body_text == migrated_body
    assert "\r" not in outbound.body_text
    assert outbound.content_revision == 3


@pytest.mark.django_db
def test_manual_approval_migration_updates_existing_unsent_drafts(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    _, _, outbound, _ = _operational_data(owner)
    OutboundMessage.objects.filter(pk=outbound.pk).update(
        subject="PUBLICIDAD - Consulta técnica",
        body_text=(
            "Contenido conservado.\nFran · Carbones SA\nCarbones SA · CABA\n"
            "Si no querés recibir más mensajes, respondé BAJA."
        ),
        state=OutboundMessage.State.PREPARED,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        next_attempt_at=timezone.now(),
    )

    migration = import_module("apps.campaigns.migrations.0011_outbound_manual_approval")
    migration.prepare_existing_drafts(django_apps, None)

    outbound.refresh_from_db()
    assert outbound.subject == "Consulta técnica"
    assert outbound.body_text.endswith("Carbones SA · CABA")
    assert "BAJA" not in outbound.body_text
    assert outbound.state == OutboundMessage.State.REVIEW_READY
    assert outbound.next_attempt_at is None
    assert outbound.content_revision == 2


@pytest.mark.django_db
def test_overture_provenance_is_visible_on_prospect_campaign_and_message_pages(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    snapshot = OvertureDatasetSnapshot.objects.create(
        release_id="2026-07-22.0",
        schema_version="places-v2",
        taxonomy_version="taxonomy-v1",
        importer_version="contact-outreach-v1",
        mapping_version="category-map-v2",
        boundary_version="caba-v1",
        boundary_manifest_sha256="b" * 64,
        source_uri=("s3://overturemaps-us-west-2/release/2026-07-22.0/theme=places/type=place/"),
        manifest_sha256="a" * 64,
        status=OvertureDatasetSnapshot.Status.READY,
        is_active=True,
        source_licenses=["CDLA Permissive 2.0"],
        attribution="© Overture Maps Foundation y sus colaboradores",
    )
    campaign, prospect, outbound, _ = _operational_data(owner)
    Campaign.objects.filter(pk=campaign.pk).update(
        extractor_provider="overture",
        overture_snapshot=snapshot,
    )
    provider_data = {
        "overture_id": "08f2a1072b1142d003f8",
        "snapshot": {
            "id": str(snapshot.pk),
            "release_id": snapshot.release_id,
            "schema_version": snapshot.schema_version,
            "taxonomy_version": snapshot.taxonomy_version,
            "importer_version": snapshot.importer_version,
            "attribution": snapshot.attribution,
            "source_licenses": snapshot.source_licenses,
        },
        "zone": {
            "name": "Palermo",
            "attribution": "Buenos Aires Data · CC-BY-2.5-AR",
        },
        "matched_rule": {
            "taxonomy_code": "auto_electrical_repair",
            "name_terms": ["bobinad*"],
        },
        "matched_rule_index": 2,
        "match_quality": 2,
        "confidence": "0.9130",
        "provenance": {
            "field_provenance": {
                "/names/primary": [
                    {
                        "dataset": "meta",
                        "record_id": "source-record-7",
                    }
                ]
            },
            "source_licenses": ["CDLA Permissive 2.0"],
        },
    }
    Prospect.objects.filter(pk=prospect.pk).update(
        provider_data=provider_data,
        website="https://taller.example/",
    )
    ProspectEmail.objects.filter(pk=outbound.prospect_email_id).update(
        source="website_mailto",
        source_url="https://taller.example/contacto",
        source_content_hash="c" * 64,
    )
    WebsiteSnapshot.objects.create(
        prospect=prospect,
        requested_url="https://taller.example/",
        final_url="https://taller.example/contacto",
        fetched_at=timezone.now(),
        http_status=200,
        content_type="text/html",
        content_hash="d" * 64,
        pages=[],
        status=WebsiteSnapshot.Status.SUCCESS,
    )
    client.force_login(owner)

    responses = (
        client.get(reverse("prospects"), {"campaign": str(campaign.pk)}),
        client.get(reverse("campaign-detail", args=(campaign.pk,))),
        client.get(reverse("outbound-detail", args=(outbound.pk,))),
    )

    for response in responses:
        content = response.content.decode()
        assert response.status_code == 200
        assert "2026-07-22.0" in content
        assert str(snapshot.pk) in content
        assert "08f2a1072b1142d003f8" in content
        assert "auto_electrical_repair" in content
        assert "bobinad*" in content
        assert "/names/primary" in content
        assert "source-record-7" in content
        assert "sitio web, enlace directo de correo" in content
        assert "https://taller.example/contacto" in content
        assert "CDLA Permissive 2.0" in content
        assert "© Overture Maps Foundation y sus colaboradores" in content
        assert "Buenos Aires Data · CC-BY-2.5-AR" in content


@pytest.mark.django_db
def test_fake_and_legacy_prospects_render_without_overture_provenance(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign, _, outbound, _ = _operational_data(owner)
    client.force_login(owner)

    pages = (
        client.get(reverse("prospects"), {"campaign": str(campaign.pk)}),
        client.get(reverse("campaign-detail", args=(campaign.pk,))),
        client.get(reverse("outbound-detail", args=(outbound.pk,))),
    )

    assert all(page.status_code == 200 for page in pages)
    assert all("no contiene procedencia Overture" in page.content.decode() for page in pages)


@pytest.mark.django_db
def test_failed_delivery_retry_is_explicit_same_row_and_audited(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    _, _, outbound, _ = _operational_data(owner)
    job = BackgroundJob.objects.create(
        task_name="mailbox.deliver_message",
        idempotency_key=f"deliver:{outbound.pk}",
        entity_type="OutboundMessage",
        entity_id=str(outbound.pk),
        state=BackgroundJob.State.FAILED,
        error="Proveedor corregible",
    )
    client.force_login(owner)
    page = client.get(reverse("jobs"), {"state": "FAILED", "q": outbound.pk})
    assert list(page.context["page_obj"]) == [job]

    rejected = client.post(reverse("job-retry", args=(job.pk,)), {"reason": "corto"})
    assert rejected.status_code == 302
    outbound.refresh_from_db()
    assert outbound.state == OutboundMessage.State.SEND_FAILED

    accepted = client.post(
        reverse("job-retry", args=(job.pk,)),
        {"reason": "Se restauró la conexión del proveedor"},
    )
    assert accepted.status_code == 302
    outbound.refresh_from_db()
    job.refresh_from_db()
    assert outbound.state == OutboundMessage.State.REVIEW_READY
    assert outbound.approved_at is None
    assert outbound.attempts == 0
    assert job.state == BackgroundJob.State.CANCELLED
    assert AuditEvent.objects.filter(action="message.retry_requested").exists()


def test_json_logs_redact_secrets_and_include_correlation() -> None:
    formatter = RedactingJsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="password=super-secret Bearer abc.def token:visible",
        args=(),
        exc_info=None,
    )
    token = correlation_id_var.set("corr-123")
    try:
        payload = json.loads(formatter.format(record))
    finally:
        correlation_id_var.reset(token)
    assert payload["correlation_id"] == "corr-123"
    assert "super-secret" not in payload["message"]
    assert "abc.def" not in payload["message"]
    assert "visible" not in payload["message"]


def test_error_pages_do_not_expose_exception_details() -> None:
    from apps.core.views import bad_request, page_not_found, permission_denied, server_error

    request = RequestFactory().get("/faltante/")
    request.correlation_id = "safe-correlation"  # type: ignore[attr-defined]
    for response in (
        bad_request(request, ValueError("secret")),
        permission_denied(request, ValueError("secret")),
        page_not_found(request, ValueError("secret")),
        server_error(request),
    ):
        assert response.status_code in {400, 403, 404, 500}
        assert b"secret" not in response.content
        assert b"safe-correlation" in response.content
