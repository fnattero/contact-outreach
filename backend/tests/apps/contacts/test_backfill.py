from __future__ import annotations

import importlib
import uuid

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.campaigns.models import Campaign, OutboundMessage, SearchQuery, SearchRun
from apps.catalogs.models import Catalog
from apps.compliance.models import SuppressionEntry
from apps.contacts.models import (
    CampaignEnrollment,
    CommunicationRestriction,
    Contact,
    Conversation,
    EmailAddress,
    Organization,
    OrganizationIdentity,
)
from apps.mailbox.models import GmailConnection, InboundMessage
from apps.prospects.models import Prospect, ProspectEmail, ProspectIdentity


def _migrate(targets: list[tuple[str, str | None]]):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    state_targets = [target for target in targets if target[1] is not None]
    return executor.loader.project_state(state_targets).apps


def _campaign(*, owner, catalog: Catalog, suffix: str) -> tuple[Campaign, SearchRun]:
    campaign = Campaign.objects.create(
        name=f"Campaña {suffix}",
        catalog=catalog,
        created_by=owner,
    )
    query = SearchQuery.objects.create(
        campaign=campaign,
        category_snapshot="Talleres",
        zone_snapshot="Palermo",
        location_snapshot="CABA",
        query_text=f"consulta {suffix}",
        normalized_query=f"consulta-{suffix}",
    )
    run = SearchRun.objects.create(
        campaign=campaign,
        query=query,
        provider="overture",
        idempotency_key=f"run-{suffix}-{uuid.uuid4()}",
        request_json={},
        requested_limit=20,
    )
    return campaign, run


def _prospect(
    *,
    campaign: Campaign,
    run: SearchRun,
    suffix: str,
    domain: str,
    email: str,
) -> tuple[Prospect, ProspectEmail]:
    now = timezone.now()
    prospect = Prospect.objects.create(
        campaign=campaign,
        source_run=run,
        name=f"Taller {suffix}",
        normalized_name=f"taller {suffix.casefold()}",
        address=f"Calle {suffix} 123",
        normalized_address=f"calle {suffix.casefold()} 123",
        business_domain=domain,
        pipeline_state=Prospect.PipelineState.QUEUED,
    )
    prospect_email = ProspectEmail.objects.create(
        prospect=prospect,
        original_email=email,
        normalized_email=email,
        domain=email.rsplit("@", 1)[-1],
        local_part=email.split("@", 1)[0],
        source="OVERTURE",
        mx_status=ProspectEmail.MXStatus.VALID,
        mx_checked_at=now,
        is_primary=True,
    )
    return prospect, prospect_email


def _outbound(
    *,
    campaign: Campaign,
    prospect: Prospect,
    prospect_email: ProspectEmail,
    catalog: Catalog,
    suffix: str,
) -> OutboundMessage:
    now = timezone.now()
    return OutboundMessage.objects.create(
        campaign=campaign,
        prospect=prospect,
        prospect_email=prospect_email,
        recipient=prospect_email.original_email,
        recipient_normalized=prospect_email.normalized_email,
        subject="Propuesta comercial",
        body_text="Mensaje histórico",
        catalog=catalog,
        catalog_version=catalog.version,
        state=OutboundMessage.State.SENT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key=f"message-{suffix}-{uuid.uuid4()}",
        message_id=f"<message-{suffix}@example.invalid>",
        gmail_message_id=f"gmail-{suffix}",
        gmail_thread_id=f"thread-{suffix}",
        sent_at=now,
    )


@pytest.mark.django_db
def test_backfill_is_idempotent_and_preserves_legacy_message_identity(owner) -> None:
    now = timezone.now()
    catalog = Catalog.objects.create(
        name="Catálogo histórico",
        version=1,
        file="catalogs/historico.pdf",
        original_filename="historico.pdf",
        detected_mime="application/pdf",
        byte_size=100,
        sha256="a" * 64,
        uploaded_by=owner,
    )
    campaign_one, run_one = _campaign(owner=owner, catalog=catalog, suffix="uno")
    campaign_two, run_two = _campaign(owner=owner, catalog=catalog, suffix="dos")
    campaign_auto, run_auto = _campaign(owner=owner, catalog=catalog, suffix="auto")
    prospect_one, email_one = _prospect(
        campaign=campaign_one,
        run=run_one,
        suffix="Uno",
        domain="taller.example",
        email="ventas@taller.example",
    )
    prospect_two, email_two = _prospect(
        campaign=campaign_two,
        run=run_two,
        suffix="Dos",
        domain="taller.example",
        email="info@taller.example",
    )
    prospect_auto, email_auto = _prospect(
        campaign=campaign_auto,
        run=run_auto,
        suffix="Auto",
        domain="auto.example",
        email="info@auto.example",
    )
    provider_hash = "b" * 64
    ProspectIdentity.objects.create(
        prospect=prospect_one,
        kind=ProspectIdentity.Kind.PROVIDER_ID,
        value_hash=provider_hash,
    )
    outbound_one = _outbound(
        campaign=campaign_one,
        prospect=prospect_one,
        prospect_email=email_one,
        catalog=catalog,
        suffix="uno",
    )
    _outbound(
        campaign=campaign_two,
        prospect=prospect_two,
        prospect_email=email_two,
        catalog=catalog,
        suffix="dos",
    )
    outbound_auto = _outbound(
        campaign=campaign_auto,
        prospect=prospect_auto,
        prospect_email=email_auto,
        catalog=catalog,
        suffix="auto",
    )
    connection = GmailConnection.objects.create(
        owner=owner,
        email="owner@example.invalid",
        scopes=[],
        refresh_token_encrypted="encrypted",
        status=GmailConnection.Status.CONNECTED,
        last_tested_at=now,
    )
    human = InboundMessage.objects.create(
        connection=connection,
        related_outbound=outbound_one,
        gmail_message_id="gmail-human",
        gmail_thread_id=outbound_one.gmail_thread_id,
        message_id="<human@example.invalid>",
        in_reply_to=outbound_one.message_id,
        sender="Compras <compras@taller.example>",
        recipients=[connection.email],
        subject="Re: Propuesta comercial",
        external_at=now,
        received_at=now,
        body_text="Me interesa.",
        classification=InboundMessage.Classification.INTERESTED,
        is_human=True,
    )
    automatic = InboundMessage.objects.create(
        connection=connection,
        related_outbound=outbound_auto,
        gmail_message_id="gmail-auto",
        gmail_thread_id=outbound_auto.gmail_thread_id,
        message_id="<auto@example.invalid>",
        in_reply_to=outbound_auto.message_id,
        sender="info@auto.example",
        recipients=[connection.email],
        subject="Respuesta automática",
        external_at=now,
        received_at=now,
        body_text="Fuera de la oficina.",
        classification=InboundMessage.Classification.AUTO_REPLY,
        is_human=False,
    )
    SuppressionEntry.objects.create(
        original_email="actual@example.net",
        normalized_email="actual@example.net",
        reason=SuppressionEntry.Reason.MANUAL,
        source="dashboard",
        evidence="Cliente cargado anteriormente",
        created_by=owner,
    )
    SuppressionEntry.objects.create(
        original_email="rebote@example.net",
        normalized_email="rebote@example.net",
        reason=SuppressionEntry.Reason.BOUNCE,
        source="gmail",
        evidence="550 mailbox unavailable",
    )
    legacy_counts = {
        "prospects": Prospect.objects.count(),
        "outbound": OutboundMessage.objects.count(),
        "inbound": InboundMessage.objects.count(),
        "suppressions": SuppressionEntry.objects.count(),
    }
    preserved_ids = {
        "outbound_pk": outbound_one.pk,
        "gmail_id": outbound_one.gmail_message_id,
        "message_id": outbound_one.message_id,
        "inbound_pk": human.pk,
    }

    migration = importlib.import_module("apps.contacts.migrations.0002_backfill_contact_foundation")
    migration.backfill_contact_foundation(django_apps, None)
    first_counts = {
        "organizations": Organization.objects.count(),
        "emails": EmailAddress.objects.count(),
        "contacts": Contact.objects.count(),
        "enrollments": CampaignEnrollment.objects.count(),
        "conversations": Conversation.objects.count(),
        "restrictions": CommunicationRestriction.objects.count(),
    }
    migration.backfill_contact_foundation(django_apps, None)

    prospect_one.refresh_from_db()
    prospect_two.refresh_from_db()
    human.refresh_from_db()
    automatic.refresh_from_db()
    outbound_one.refresh_from_db()
    assert prospect_one.organization_id == prospect_two.organization_id
    assert prospect_one.campaign_enrollment_id is not None
    assert prospect_two.campaign_enrollment_id is not None
    assert OrganizationIdentity.objects.filter(
        organization_id=prospect_one.organization_id,
        kind=OrganizationIdentity.Kind.GERS_ID,
        value_hash=provider_hash,
    ).exists()
    assert human.contact_id is not None
    assert human.conversation_id is not None
    assert outbound_one.conversation_id == human.conversation_id
    assert Conversation.objects.get(pk=human.conversation_id).gmail_thread_id == "thread-uno"
    assert automatic.contact_id is None
    assert not Contact.objects.filter(organization_id=prospect_auto.organization_id).exists()
    assert Contact.objects.filter(
        preferred_email__normalized_email="actual@example.net",
        created_reason=Contact.CreatedReason.MANUAL_RESTRICTION,
    ).exists()
    bounced = EmailAddress.objects.get(normalized_email="rebote@example.net")
    assert bounced.validity == EmailAddress.Validity.INVALID
    assert not Contact.objects.filter(organization=bounced.organization).exists()
    assert CommunicationRestriction.objects.filter(
        email_address=bounced,
        kind=CommunicationRestriction.Kind.BOUNCE,
        revoked_at__isnull=True,
    ).exists()
    assert first_counts == {
        "organizations": Organization.objects.count(),
        "emails": EmailAddress.objects.count(),
        "contacts": Contact.objects.count(),
        "enrollments": CampaignEnrollment.objects.count(),
        "conversations": Conversation.objects.count(),
        "restrictions": CommunicationRestriction.objects.count(),
    }
    assert legacy_counts == {
        "prospects": Prospect.objects.count(),
        "outbound": OutboundMessage.objects.count(),
        "inbound": InboundMessage.objects.count(),
        "suppressions": SuppressionEntry.objects.count(),
    }
    assert outbound_one.pk == preserved_ids["outbound_pk"]
    assert outbound_one.gmail_message_id == preserved_ids["gmail_id"]
    assert outbound_one.message_id == preserved_ids["message_id"]
    assert human.pk == preserved_ids["inbound_pk"]


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_schema_upgrade_promotes_a_manual_suppression_without_deleting_history() -> None:
    latest_targets = MigrationExecutor(connection).loader.graph.leaf_nodes()
    old_targets: list[tuple[str, str | None]] = [
        ("accounts", "0001_initial"),
        ("campaigns", "0011_outbound_manual_approval"),
        ("compliance", "0002_contactledger_contactoverride"),
        ("contacts", None),
        ("mailbox", "0002_fakegmailmessage_body_html_and_more"),
        ("prospects", "0004_website_email_provenance"),
    ]
    old_apps = _migrate(old_targets)
    try:
        User = old_apps.get_model("auth", "User")
        Suppression = old_apps.get_model("compliance", "SuppressionEntry")
        owner = User.objects.create(username="legacy-contact-owner")
        suppression = Suppression.objects.create(
            original_email="cliente-historico@example.com",
            normalized_email="cliente-historico@example.com",
            reason="MANUAL",
            source="dashboard",
            evidence="Ya era cliente antes de la migración",
            created_by_id=owner.pk,
        )

        new_apps = _migrate(
            [
                ("campaigns", "0012_outboundmessage_campaign_enrollment_and_more"),
                ("contacts", "0002_backfill_contact_foundation"),
                ("mailbox", "0003_inboundmessage_campaign_enrollment_and_more"),
                ("prospects", "0005_prospect_campaign_enrollment_prospect_organization"),
            ]
        )
        NewSuppression = new_apps.get_model("compliance", "SuppressionEntry")
        NewContact = new_apps.get_model("contacts", "Contact")
        NewEmailAddress = new_apps.get_model("contacts", "EmailAddress")
        NewRestriction = new_apps.get_model("contacts", "CommunicationRestriction")

        assert NewSuppression.objects.filter(pk=suppression.pk).exists()
        email_address = NewEmailAddress.objects.get(
            normalized_email="cliente-historico@example.com"
        )
        contact = NewContact.objects.get(organization_id=email_address.organization_id)
        assert contact.created_reason == "MANUAL_RESTRICTION"
        restriction = NewRestriction.objects.get(email_address_id=email_address.pk, kind="MANUAL")
        assert restriction.evidence == "Ya era cliente antes de la migración"
        assert restriction.created_by_id == owner.pk
    finally:
        _migrate(latest_targets)
