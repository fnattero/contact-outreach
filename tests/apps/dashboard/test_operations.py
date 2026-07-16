from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent, BackgroundJob
from apps.audit.observability import RedactingJsonFormatter, correlation_id_var
from apps.audit.services import record_event
from apps.campaigns.models import Campaign, OutboundMessage, SearchQuery, SearchRun
from apps.catalogs.services import create_catalog
from apps.dashboard.csv_export import spreadsheet_safe
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import GmailConnection, InboundMessage
from apps.prospects.models import AIAnalysis, Prospect, ProspectEmail


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
    client.force_login(owner)

    dashboard = client.get(reverse("dashboard"), {"campaign": campaign.pk})
    assert dashboard.status_code == 200
    assert dashboard.context["metrics"]["raw"] == 3
    assert dashboard.context["metrics"]["interested"] == 1
    assert dashboard.context["metrics"]["sent"] == 0
    assert dashboard.context["metrics"]["failed"] == 1
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
    assert outbound.state == OutboundMessage.State.QUEUED
    assert outbound.attempts == 0
    assert job.state == BackgroundJob.State.RETRY_WAIT
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
