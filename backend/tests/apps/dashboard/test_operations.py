from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts import permissions
from apps.audit.models import AuditEvent, BackgroundJob
from apps.audit.observability import RedactingJsonFormatter, correlation_id_var
from apps.campaigns.delivery import retry_failed_message
from apps.campaigns.models import Campaign, OutboundMessage, SearchQuery, SearchRun
from apps.catalogs.services import create_catalog
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import GmailConnection, InboundMessage
from apps.prospects.models import AIAnalysis, Prospect, ProspectEmail


def _api(client: Client, method: str, url: str, payload: dict[str, object] | None = None, **extra):
    """Call the JSON API as an already signed-in client (CSRF is not enforced on this client)."""
    kwargs = {"content_type": "application/json", **extra}
    if method == "get":
        return client.get(url, payload or {}, **extra)
    return getattr(client, method)(url, data=json.dumps(payload or {}), **kwargs)


def _idempotent() -> dict[str, str]:
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


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
        "Te contacto porque trabajamos con componentes industriales para equipos "
        "eléctricos y queremos "
        "conversar sobre una posible aplicación en la actividad del negocio. Contamos con "
        "distintas medidas y alternativas para tareas de reparación y mantenimiento, sin "
        "asumir qué modelos utilizan actualmente. La idea es que nuestro vendedor pueda "
        "acercarse, conocer la necesidad concreta y mostrar el catálogo técnico disponible. "
        "¿Qué día conviene que pase el vendedor?\n\n"
        "Vendedor · Componentes Delta SA\nComponentes Delta SA · CABA"
    )


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

    campaign_view = _api(client, "get", reverse("api-campaign-detail", args=(campaign.pk,)))
    listing = _api(
        client,
        "get",
        reverse("api-outbound-messages"),
        {"campaign": campaign.pk, "state": OutboundMessage.State.REVIEW_READY},
    )
    detail = _api(client, "get", reverse("api-outbound-message-detail", args=(outbound.pk,)))

    metrics = campaign_view.json()["data"]["metrics"]
    assert metrics["review_ready"] == 1
    assert metrics["queued"] == 0
    assert [row["id"] for row in listing.json()["data"]] == [str(outbound.pk)]
    assert listing.json()["data"][0]["state_label"] == "Listo para revisar"
    # A message waiting for review has not been sent.
    assert detail.json()["data"]["state"] == OutboundMessage.State.REVIEW_READY
    assert detail.json()["data"]["sent_at"] is None


@pytest.mark.django_db
def test_live_draft_can_be_edited_then_requires_audited_approval(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign, _, outbound, _ = _operational_data(owner)
    Campaign.objects.filter(pk=campaign.pk).update(
        profile_snapshot={
            "company_name": "Componentes Delta SA",
            "salesperson_name": "Vendedor",
            "address": "CABA",
            "signature": "Vendedor · Componentes Delta SA",
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
    draft = reverse("api-outbound-message-draft", args=(outbound.pk,))
    authorize = reverse("api-outbound-message-authorize", args=(outbound.pk,))

    detail = _api(client, "get", reverse("api-outbound-message-detail", args=(outbound.pk,)))
    assert detail.status_code == 200
    assert detail.json()["data"]["state"] == OutboundMessage.State.REVIEW_READY
    assert detail.json()["data"]["approved_at"] is None

    invalid = _api(client, "patch", draft, {"subject": "Cambio inválido", "body_text": "Sin firma"})
    assert invalid.status_code == 400
    outbound.refresh_from_db()
    assert outbound.subject == "Consulta técnica"

    edited_body = _reviewable_body().replace("queremos conversar", "preferimos conversar")
    edited = _api(
        client, "patch", draft, {"subject": "Consulta para coordinar", "body_text": edited_body}
    )
    assert edited.status_code == 200, edited.content
    outbound.refresh_from_db()
    assert outbound.subject == "Consulta para coordinar"
    assert outbound.body_text == edited_body
    assert outbound.content_revision == 2
    assert outbound.last_edited_by == owner
    assert outbound.state == OutboundMessage.State.REVIEW_READY
    assert outbound.approved_at is None

    approved = _api(client, "post", authorize, **_idempotent())
    assert approved.status_code == 200, approved.content
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

    assert _api(client, "patch", draft, {}).status_code == 400
    # An approved message cannot be approved a second time under a new idempotency key.
    assert _api(client, "post", authorize, **_idempotent()).status_code == 400
    outbound.refresh_from_db()
    assert outbound.state == OutboundMessage.State.QUEUED

    other = User.objects.create_user(username="review-other", password="password")
    client.force_login(other)
    assert _api(client, "patch", draft, {}).status_code == 403
    assert _api(client, "post", authorize, **_idempotent()).status_code == 403


@pytest.mark.django_db
def test_draft_editor_normalizes_browser_crlf_and_accepts_migrated_short_copy(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign, _, outbound, _ = _operational_data(owner)
    Campaign.objects.filter(pk=campaign.pk).update(
        profile_snapshot={
            "company_name": "Componentes Delta SA",
            "salesperson_name": "Vendedor",
            "address": "CABA",
            "signature": "Vendedor · Componentes Delta SA",
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

    edited = _api(
        client,
        "patch",
        reverse("api-outbound-message-draft", args=(outbound.pk,)),
        {
            "subject": "Consulta técnica actualizada",
            "body_text": migrated_body.replace("\n", "\r\n"),
        },
    )

    assert edited.status_code == 200, edited.content
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
            "Contenido conservado.\nVendedor · Componentes Delta SA\nComponentes Delta SA · CABA\n"
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
    assert outbound.body_text.endswith("Componentes Delta SA · CABA")
    assert "BAJA" not in outbound.body_text
    assert outbound.state == OutboundMessage.State.REVIEW_READY
    assert outbound.next_attempt_at is None
    assert outbound.content_revision == 2


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
    page = _api(
        client, "get", reverse("api-background-jobs"), {"state": "FAILED", "q": str(outbound.pk)}
    )
    assert [row["id"] for row in page.json()["data"]] == [str(job.pk)]

    retry = reverse("api-background-job-retry", args=(job.pk,))
    rejected = _api(client, "post", retry, {"reason": "corto"}, **_idempotent())
    assert rejected.status_code == 400
    outbound.refresh_from_db()
    assert outbound.state == OutboundMessage.State.SEND_FAILED

    accepted = _api(
        client, "post", retry, {"reason": "Se restauró la conexión del proveedor"}, **_idempotent()
    )
    assert accepted.status_code == 200, accepted.content
    outbound.refresh_from_db()
    job.refresh_from_db()
    assert outbound.state == OutboundMessage.State.REVIEW_READY
    assert outbound.approved_at is None
    assert outbound.attempts == 0
    assert job.state == BackgroundJob.State.CANCELLED
    assert AuditEvent.objects.filter(action="message.retry_requested").exists()


@pytest.mark.django_db
def test_viewing_jobs_does_not_allow_retrying_a_send(
    client: Client,
    owner: User,
    private_catalog_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    seller = User.objects.create_user(username="jobs-viewer", password="viewer-password-1")
    # Even a role that can read jobs must not be able to resend an email.
    monkeypatch.setattr(
        permissions,
        "VENDEDOR_CAPABILITIES",
        permissions.VENDEDOR_CAPABILITIES | {permissions.Capability.VIEW_JOBS},
    )
    client.force_login(seller)

    listing = _api(client, "get", reverse("api-background-jobs"))
    assert listing.status_code == 200

    retry = _api(
        client,
        "post",
        reverse("api-background-job-retry", args=(job.pk,)),
        {"reason": "Se restauró la conexión del proveedor"},
        **_idempotent(),
    )
    assert retry.status_code == 403
    with pytest.raises(PermissionDenied):
        retry_failed_message(
            outbound.pk, actor=seller, reason="Se restauró la conexión del proveedor"
        )
    outbound.refresh_from_db()
    assert outbound.state == OutboundMessage.State.SEND_FAILED


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
