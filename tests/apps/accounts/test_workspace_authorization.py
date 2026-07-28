from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ActivationToken, Membership
from apps.accounts.services import (
    change_membership_role,
    create_managed_user,
    issue_activation_token,
    set_user_active,
    unlock_login,
)
from apps.campaigns.delivery import retry_failed_message
from apps.campaigns.models import Campaign, OutboundMessage, SearchQuery, SearchRun
from apps.campaigns.services import transition_campaign
from apps.catalogs.models import Catalog
from apps.catalogs.services import create_catalog
from apps.compliance.services import suppress_email
from apps.configuration.integrations import (
    runtime_integration_configuration,
    save_integration_configuration,
)
from apps.configuration.models import BusinessProfile, IntegrationConfiguration
from apps.configuration.services import save_business_profile, save_prompt_configuration
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.manual import authorize_manual_reply
from apps.mailbox.models import GmailConnection, InboundMessage
from apps.prospects.models import Prospect, ProspectEmail


def _catalog(admin: User, *, name: str = "Catálogo compartido") -> Catalog:
    return create_catalog(
        name=name,
        upload=SimpleUploadedFile(
            f"{name}.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
            content_type="application/pdf",
        ),
        actor=admin,
    )


def _campaign(admin: User, catalog: Catalog, *, name: str, state: str) -> Campaign:
    return Campaign.objects.create(
        name=name,
        state=state,
        discovery_state=Campaign.DiscoveryState.EXHAUSTED_QUERIES,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        catalog=catalog,
        created_by=admin,
    )


def _thread(
    admin: User,
    campaign: Campaign,
    catalog: Catalog,
    *,
    message_state: str = OutboundMessage.State.SENT,
    suffix: str = "sent",
) -> tuple[OutboundMessage, InboundMessage | None]:
    query = SearchQuery.objects.create(
        campaign=campaign,
        category_snapshot="Taller",
        zone_snapshot="Palermo",
        location_snapshot="CABA",
        query_text=f"Taller {suffix}",
        normalized_query=f"taller-{suffix}",
    )
    run = SearchRun.objects.create(
        campaign=campaign,
        query=query,
        provider="fake",
        idempotency_key=f"run:{suffix}",
        requested_limit=1,
        state=SearchRun.State.SUCCEEDED,
    )
    prospect = Prospect.objects.create(
        campaign=campaign,
        source_run=run,
        name="Taller visible",
        normalized_name="taller visible",
    )
    email = ProspectEmail.objects.create(
        prospect=prospect,
        original_email=f"{suffix}@example.com",
        normalized_email=f"{suffix}@example.com",
        domain="example.com",
        local_part=suffix,
        source="fixture",
        mx_status=ProspectEmail.MXStatus.VALID,
        mx_checked_at=timezone.now(),
        is_primary=True,
    )
    outbound = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.FIRST_CONTACT,
        campaign=campaign,
        prospect=prospect,
        prospect_email=email,
        recipient=email.original_email,
        recipient_normalized=email.normalized_email,
        subject=f"Propuesta {suffix}",
        body_text=f"Contenido {suffix}",
        catalog=catalog,
        catalog_version=catalog.version,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        state=message_state,
        idempotency_key=f"message:{suffix}",
        message_id=f"<{suffix}@example.invalid>",
        gmail_message_id=f"gmail-{suffix}" if message_state == OutboundMessage.State.SENT else "",
        gmail_thread_id=f"thread-{suffix}",
        sent_at=timezone.now() if message_state == OutboundMessage.State.SENT else None,
        error="Fallo técnico reservado a administradores"
        if message_state == OutboundMessage.State.SEND_FAILED
        else "",
    )
    if message_state != OutboundMessage.State.SENT:
        return outbound, None
    connection = GmailConnection.objects.filter(workspace=campaign.workspace).first()
    if connection is None:
        connection = GmailConnection.objects.create(
            owner=admin,
            email="shared@example.com",
            scopes=[],
            refresh_token_encrypted=encrypt_token("refresh"),
            status=GmailConnection.Status.CONNECTED,
        )
    inbound = InboundMessage.objects.create(
        connection=connection,
        related_outbound=outbound,
        gmail_message_id=f"inbound-{suffix}",
        gmail_thread_id=outbound.gmail_thread_id,
        message_id=f"<inbound-{suffix}@example.invalid>",
        in_reply_to=outbound.message_id,
        sender="Cliente <cliente@example.com>",
        recipients=[connection.email],
        subject=f"Re: {outbound.subject}",
        external_at=datetime(2026, 7, 26, 12, tzinfo=UTC),
        received_at=timezone.now(),
        body_text="Respuesta visible del cliente",
        classification=InboundMessage.Classification.INTERESTED,
        classification_confidence="0.950",
        is_human=True,
    )
    return outbound, inbound


@pytest.mark.django_db
def test_vendedor_route_method_matrix_and_private_timelines(
    client: Client,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    admin = User.objects.create_user(username="admin", password="password")
    seller = User.objects.create_user(username="seller", password="password")
    catalog = _catalog(admin)
    draft = _campaign(admin, catalog, name="Borrador secreto", state=Campaign.State.DRAFT)
    campaign = _campaign(admin, catalog, name="Campaña visible", state=Campaign.State.PAUSED)
    sent, inbound = _thread(admin, campaign, catalog)
    unsent, _ = _thread(
        admin,
        campaign,
        catalog,
        message_state=OutboundMessage.State.SEND_FAILED,
        suffix="unsent",
    )
    assert inbound is not None
    client.force_login(seller)

    readable = (
        reverse("dashboard"),
        reverse("campaigns"),
        reverse("campaign-detail", args=(campaign.pk,)),
        reverse("outbound-messages"),
        reverse("outbound-detail", args=(sent.pk,)),
        reverse("responses"),
        reverse("response-thread", args=(inbound.pk,)),
    )
    for url in readable:
        response = client.get(url)
        assert response.status_code == 200, url
        assert "private" in response.headers["Cache-Control"]
        assert "no-store" in response.headers["Cache-Control"]

    campaign_list = client.get(reverse("campaigns"))
    campaign_page = client.get(reverse("campaign-detail", args=(campaign.pk,)))
    sent_page = client.get(reverse("outbound-detail", args=(sent.pk,)))
    thread_page = client.get(reverse("response-thread", args=(inbound.pk,)))
    assert "Campaña visible" in campaign_list.content.decode()
    assert "Borrador secreto" not in campaign_list.content.decode()
    assert "Nueva campaña" not in campaign_list.content.decode()
    assert "Audiencia y consultas" not in campaign_page.content.decode()
    assert "Configuración congelada" not in campaign_page.content.decode()
    assert "Ver prospectos" not in campaign_page.content.decode()
    assert "Contenido sent" in sent_page.content.decode()
    assert "Descargar PDF" not in sent_page.content.decode()
    assert "Detalles técnicos" not in sent_page.content.decode()
    assert "Respuesta visible del cliente" in thread_page.content.decode()
    assert "Responder manualmente" not in thread_page.content.decode()
    assert client.get(reverse("campaign-detail", args=(draft.pk,))).status_code == 404
    assert client.get(reverse("outbound-detail", args=(unsent.pk,))).status_code == 404

    for url in readable:
        assert client.post(url).status_code == 405, url

    forbidden_gets = (
        reverse("account-users"),
        reverse("campaign-create"),
        reverse("prospects"),
        reverse("prospects-export"),
        reverse("outbound-export"),
        reverse("responses-export"),
        reverse("business-profile"),
        reverse("prompts"),
        reverse("integrations"),
        reverse("overture-datasets"),
        reverse("categories"),
        reverse("zones"),
        reverse("catalogs"),
        reverse("catalog-download", args=(catalog.pk,)),
        reverse("suppressions"),
        reverse("audit-log"),
        reverse("jobs"),
        reverse("gmail-settings"),
        reverse("health-degraded"),
    )
    for url in forbidden_gets:
        assert client.get(url).status_code == 403, url

    unknown_id = UUID(int=0)
    forbidden_posts = (
        reverse("campaign-create"),
        reverse("campaign-action", args=(campaign.pk, "cancel")),
        reverse("outbound-edit", args=(unsent.pk,)),
        reverse("outbound-approve", args=(unsent.pk,)),
        reverse("manual-reply", args=(inbound.pk,)),
        reverse("gmail-connect"),
        reverse("gmail-test"),
        reverse("gmail-disconnect"),
        reverse("gmail-fake-inbound"),
        reverse("overture-sync"),
        reverse("config-toggle", args=("searchcategory", unknown_id)),
        reverse("config-delete", args=("searchcategory", unknown_id)),
        reverse("job-retry", args=(unknown_id,)),
        reverse("account-user-unlock"),
    )
    for url in forbidden_posts:
        assert client.post(url).status_code == 403, url


@pytest.mark.django_db
def test_second_admin_operates_shared_workspace_resources(
    client: Client,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    creator = User.objects.create_user(username="creator", password="password")
    second_admin = User.objects.create_user(username="second-admin", password="password")
    second_admin.membership.role = Membership.Role.ADMIN
    second_admin.membership.save(update_fields=("role", "updated_at"))
    catalog = _catalog(creator)
    campaign = _campaign(
        creator,
        catalog,
        name="Campaña creada por otra persona",
        state=Campaign.State.PAUSED,
    )
    profile = save_business_profile(
        owner=creator,
        values={
            "company_name": "Empresa compartida",
            "salesperson_name": "Equipo comercial",
            "address": "Buenos Aires",
            "signature": "Equipo comercial · Empresa compartida",
        },
    )
    IntegrationConfiguration.objects.create(owner=creator, llm_model="shared-model")
    connection = GmailConnection.objects.create(
        owner=creator,
        email="shared@example.com",
        scopes=[],
        refresh_token_encrypted=encrypt_token("refresh"),
        status=GmailConnection.Status.CONNECTED,
    )

    updated = save_business_profile(
        owner=second_admin,
        values={
            "company_name": "Empresa actualizada",
            "salesperson_name": "Equipo comercial",
            "address": "Buenos Aires",
            "signature": "Equipo comercial · Empresa actualizada",
        },
    )
    assert updated.pk == profile.pk
    assert updated.owner == creator
    assert BusinessProfile.objects.count() == 1
    assert runtime_integration_configuration(second_admin.pk).llm_model == "shared-model"
    assert GmailConnection.objects.get(workspace=second_admin.membership.workspace) == connection

    client.force_login(second_admin)
    assert client.get(reverse("campaigns")).status_code == 200
    assert client.get(reverse("campaign-detail", args=(campaign.pk,))).status_code == 200
    assert client.get(reverse("business-profile")).context["profile"].pk == profile.pk
    assert client.get(reverse("gmail-settings")).context["connection"].pk == connection.pk
    assert client.get(reverse("catalog-download", args=(catalog.pk,))).status_code == 200
    changed = client.post(reverse("campaign-action", args=(campaign.pk, "cancel")))
    assert changed.status_code == 302
    campaign.refresh_from_db()
    assert campaign.state == Campaign.State.CANCELLED


@pytest.mark.django_db
def test_vendedor_cannot_bypass_permissions_through_domain_services(
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    admin = User.objects.create_user(username="service-admin", password="password")
    seller = User.objects.create_user(username="service-seller", password="password")
    catalog = _catalog(admin)
    campaign = _campaign(
        admin,
        catalog,
        name="Campaña protegida",
        state=Campaign.State.PAUSED,
    )
    failed, _ = _thread(
        admin,
        campaign,
        catalog,
        message_state=OutboundMessage.State.SEND_FAILED,
        suffix="service-failed",
    )

    with pytest.raises(PermissionDenied):
        transition_campaign(
            campaign_id=campaign.pk,
            target_state=Campaign.State.CANCELLED,
            actor=seller,
        )
    with pytest.raises(PermissionDenied):
        change_membership_role(
            membership=admin.membership,
            role=Membership.Role.VENDEDOR,
            actor=seller,
        )
    with pytest.raises(PermissionDenied):
        set_user_active(membership=admin.membership, active=False, actor=seller)
    with pytest.raises(PermissionDenied):
        create_managed_user(
            username="service-created-without-permission",
            email="blocked@example.invalid",
            role=Membership.Role.VENDEDOR,
            actor=seller,
        )
    with pytest.raises(PermissionDenied):
        issue_activation_token(
            user=admin,
            actor=seller,
            kind=ActivationToken.Kind.RESET,
        )
    with pytest.raises(PermissionDenied):
        unlock_login(username="service-admin", actor=seller)
    with pytest.raises(PermissionDenied):
        save_business_profile(owner=seller, values={})
    with pytest.raises(PermissionDenied):
        save_prompt_configuration(owner=seller, email_drafting_prompt="No autorizado")
    with pytest.raises(PermissionDenied):
        save_integration_configuration(owner=seller, values={})
    with pytest.raises(PermissionDenied):
        create_catalog(
            name="No autorizado",
            upload=SimpleUploadedFile(
                "blocked.pdf",
                b"%PDF-1.4\nblocked\n%%EOF",
                content_type="application/pdf",
            ),
            actor=seller,
        )
    with pytest.raises(PermissionDenied):
        retry_failed_message(
            failed.pk,
            actor=seller,
            reason="La causa técnica fue corregida por completo.",
        )
    with pytest.raises(PermissionDenied):
        authorize_manual_reply(
            actor=seller,
            inbound_id=uuid4(),
            body_text="No autorizado",
            request_key=uuid4(),
        )
    with pytest.raises(PermissionDenied):
        suppress_email(
            email="blocked@example.com",
            reason="MANUAL",
            evidence="No autorizado",
            actor=seller,
        )
