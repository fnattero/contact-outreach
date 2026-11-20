"""Erasing a business: nothing readable about it survives, and it can never be contacted again."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth.models import User
from django.core import serializers
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.automation.models import (
    ContactCommunicationPlan,
    ConversationMemory,
    EmailCandidate,
    FollowUpTopic,
    HumanTask,
    NotificationDelivery,
    ReplyDecision,
    ScheduledContactAttempt,
)
from apps.campaigns.models import Campaign, OutboundMessage, SearchQuery, SearchRun
from apps.catalogs.services import create_catalog
from apps.compliance.models import SuppressionEntry
from apps.contacts.erasure import drop_erased_rows, erase_organization
from apps.contacts.models import (
    CampaignEnrollment,
    CommunicationRestriction,
    Contact,
    Conversation,
    EmailAddress,
    Organization,
    OrganizationIdentity,
)
from apps.contacts.services import (
    communication_is_restricted,
    enrollment_eligibility,
    revoke_manual_restriction,
    set_contact_no_contact,
)
from apps.integrations.contracts import ExtractedBusiness, ExtractedEmail
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import GmailConnection, InboundMessage
from apps.prospects.email_validation import MockMXResolver
from apps.prospects.enrichment import _persist_enrichment
from apps.prospects.exceptions import ProspectPipelineInactive
from apps.prospects.models import (
    AIAnalysis,
    Prospect,
    ProspectRelevanceVerdict,
    WebsiteSnapshot,
)
from apps.prospects.services import IngestOutcome, ingest_business

# Distinctive words planted in every place the business's data can live. After an erasure none of
# them may appear anywhere in the database. The email address is the one thing deliberately kept.
NAME = "Taller Zafiro Quimerico"
ADDRESS = "Calle Ornitorrinco 4321, CABA"
PHONE = "+54 11 4999-8877"
WEBSITE = "https://zafiro-quimerico.example/contacto"
PERSON = "Ramona Quetzal"
SECRET = "Consulta privada sobre valvulas Quetzalcoatl"
EMAIL = "ventas@correo-negocio.example"
SENTINELS = ("zafiro", "ornitorrinco", "4999-8877", "ramona", "quetzal")
PROVIDER_ID = "place-zafiro"


def _database_text() -> str:
    """Every row of every model, as text, to search for anything that should have gone."""

    chunks = []
    for model in apps.get_models():
        rows = model._default_manager.all()
        chunks.append(serializers.serialize("json", rows, ensure_ascii=False))
    return "\n".join(chunks).casefold()


def _leaks() -> list[str]:
    text = _database_text()
    return [word for word in SENTINELS if word in text]


def _run(owner: User, *, suffix: str = "uno") -> SearchRun:
    catalog = create_catalog(
        name=f"Catálogo {suffix}",
        upload=SimpleUploadedFile(
            f"catalogo-{suffix}.pdf",
            f"%PDF-1.4\n% {suffix}\n%%EOF".encode(),
            content_type="application/pdf",
        ),
        actor=owner,
    )
    campaign = Campaign.objects.create(
        name=f"Campaña {suffix}",
        state=Campaign.State.DISCOVERING,
        discovery_state=Campaign.DiscoveryState.RUNNING,
        delivery_mode=Campaign.DeliveryMode.REVIEW_ONLY,
        extractor_provider="fake",
        website_fetcher="fake",
        llm_provider="fake",
        llm_model="fake-deterministic",
        catalog=catalog,
        created_by=owner,
    )
    query = SearchQuery.objects.create(
        campaign=campaign,
        category_snapshot="Taller",
        zone_snapshot="Palermo",
        location_snapshot="CABA",
        query_text=f"taller {suffix}",
        normalized_query=f"taller {suffix}",
    )
    return SearchRun.objects.create(
        campaign=campaign,
        query=query,
        provider="fake",
        idempotency_key=f"erasure:{suffix}",
        requested_limit=20,
    )


def _business(*, provider_id: str = PROVIDER_ID, name: str = NAME) -> ExtractedBusiness:
    return ExtractedBusiness(
        provider_id=provider_id,
        name=name,
        address=ADDRESS,
        website=WEBSITE,
        phone=PHONE,
        email_candidates=(ExtractedEmail(value=EMAIL, is_primary=True),),
        provider_data={"overture_id": provider_id, "address_data": {"street": ADDRESS}},
    )


def _discover(owner: User, *, suffix: str = "uno") -> tuple[SearchRun, Prospect]:
    run = _run(owner, suffix=suffix)
    result = ingest_business(run=run, business=_business(), resolver=MockMXResolver())
    assert result.prospect is not None
    return run, Prospect.objects.get(pk=result.prospect.pk)


def _full_history(owner: User) -> dict[str, Any]:
    """A business found by a search, written to, that replied and is being followed up."""

    run, prospect = _discover(owner)
    organization = Organization.objects.get(pk=prospect.organization_id)
    email = EmailAddress.objects.get(organization=organization)
    workspace = organization.workspace
    run.response_json = {
        "status": "SUCCEEDED",
        "data": [
            {"overture_id": PROVIDER_ID, "name": NAME, "address": ADDRESS},
            {"overture_id": "place-vecino", "name": "Vecino Conservado", "address": "Otra 1"},
        ],
    }
    run.save(update_fields=("response_json",))
    WebsiteSnapshot.objects.create(
        prospect=prospect,
        requested_url=WEBSITE,
        final_url=WEBSITE,
        fetched_at=timezone.now(),
        content_hash="a" * 64,
        status=WebsiteSnapshot.Status.SUCCESS,
        excerpt=f"{NAME} · {PHONE}",
        pages=[{"final_url": WEBSITE, "excerpt": NAME}],
        email_candidates=[{"value": EMAIL, "page_url": WEBSITE}],
    )
    ProspectRelevanceVerdict.objects.create(
        prospect=prospect,
        input_hash="b" * 64,
        criteria_digest="c" * 64,
        mode="VETO",
        provider="fake",
        model="fake",
        schema_version="1",
        status=ProspectRelevanceVerdict.Status.VALID,
        verdict=ProspectRelevanceVerdict.Verdict.FIT,
        evaluated_at=timezone.now(),
        reason=f"{NAME} repara motores",
        request_manifest={"name": NAME},
    )
    AIAnalysis.objects.create(
        prospect=prospect,
        input_hash="d" * 64,
        prompt_version="1",
        schema_version="1",
        provider="fake",
        model="fake",
        analyzed_at=timezone.now(),
        status=AIAnalysis.Status.VALID,
        prompt_text=f"Analizá {NAME} en {ADDRESS}",
        relevance_reason=NAME,
    )
    contact = Contact.objects.create(
        workspace=workspace,
        organization=organization,
        preferred_email=email,
        name=PERSON,
        created_reason=Contact.CreatedReason.HUMAN_REPLY,
    )
    connection = GmailConnection.objects.create(
        owner=owner,
        email="owner@example.invalid",
        scopes=[],
        refresh_token_encrypted=encrypt_token("refresh"),
        status=GmailConnection.Status.CONNECTED,
    )
    conversation = Conversation.objects.create(
        workspace=workspace,
        contact=contact,
        connection=connection,
        gmail_thread_id="thread-erasure",
        subject=f"Propuesta para {NAME}",
    )
    sent = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.INITIAL,
        campaign=run.campaign,
        organization=organization,
        email_address=email,
        contact=contact,
        conversation=conversation,
        recipient=f"{PERSON} <{EMAIL}>",
        recipient_normalized=EMAIL,
        subject="Propuesta comercial",
        body_text="Texto fijo de la campaña.",
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="erasure-sent",
        message_id="<sent@example.invalid>",
        state=OutboundMessage.State.SENT,
    )
    inbound = InboundMessage.objects.create(
        connection=connection,
        related_outbound=sent,
        organization=organization,
        contact=contact,
        conversation=conversation,
        gmail_message_id="inbound-erasure",
        gmail_thread_id="thread-erasure",
        sender=f"{PERSON} <{EMAIL}>",
        recipients=[connection.email],
        subject=f"Re: Propuesta para {NAME}",
        external_at=timezone.now(),
        received_at=timezone.now(),
        body_text=f"{SECRET}. Llamame al {PHONE}. {PERSON}",
        body_html_sanitized=f"<p>{SECRET}</p>",
        headers={"From": f"{PERSON} <{EMAIL}>"},
        classification=InboundMessage.Classification.INTERESTED,
        classification_confidence="0.900",
    )
    pending_reply = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.MANUAL_REPLY,
        contact=contact,
        conversation=conversation,
        organization=organization,
        email_address=email,
        parent_inbound=inbound,
        recipient=EMAIL,
        recipient_normalized=EMAIL,
        subject=f"Re: Propuesta para {NAME}",
        body_text=f"Hola {PERSON}, te paso precios.",
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="erasure-pending-reply",
        state=OutboundMessage.State.QUEUED,
    )
    EmailCandidate.objects.create(
        inbound=inbound,
        original_literal=f"{PERSON} <compras-quetzal@otro.example>",
        normalized_email="compras-quetzal@otro.example",
        region=EmailCandidate.Region.NEW_CONTENT,
        source=EmailCandidate.Source.TEXT,
        evidence_hash="e" * 64,
    )
    decision = ReplyDecision.objects.create(
        workspace=workspace,
        inbound=inbound,
        contact=contact,
        conversation=conversation,
        provider="fake",
        model="fake",
        policy_version="test",
        classification="INTERESTED",
        intent="PRICING",
        action="HUMAN",
        confidence=Decimal("0.90"),
        context_hash="f" * 64,
        proposed_body=f"Hola {PERSON}",
        human_reason="PRICING",
        context_manifest={"business": NAME},
        feedback=f"Revisar con {PERSON}",
        state=ReplyDecision.State.HUMAN_REQUIRED,
    )
    task = HumanTask.objects.create(
        workspace=workspace,
        contact=contact,
        conversation=conversation,
        inbound=inbound,
        decision=decision,
        kind="REPLY_REVIEW",
        reason=f"{PERSON} pide precios",
        friendly_summary=f"{NAME} pide precios",
        opened_at=timezone.now(),
    )
    NotificationDelivery.objects.create(
        task=task,
        recipient=owner,
        subject=f"Revisar a {NAME}",
        secure_url="https://example.invalid/attention",
        idempotency_key="erasure-notification",
        message_id="<notification@example.invalid>",
    )
    ConversationMemory.objects.create(
        contact=contact,
        conversation=conversation,
        version=1,
        summary={"facts": [f"{PERSON} compra válvulas Quetzalcoatl"]},
        source_message_ids=[str(inbound.pk)],
        source_hash="1" * 64,
    )
    topic = FollowUpTopic.objects.create(
        workspace=workspace,
        name="Seguimiento trimestral",
        objective="Preguntar cómo están.",
        cadence_days=90,
        mode=FollowUpTopic.Mode.REVIEW_BEFORE_SEND,
        next_due_at=timezone.now() + timedelta(days=10),
        active=True,
        created_by=owner,
        updated_by=owner,
    )
    plan = ContactCommunicationPlan.objects.create(
        contact=contact,
        topic=topic,
        preferred_email=email,
        state=ContactCommunicationPlan.State.ACTIVE,
        next_due_at=timezone.now(),
        created_by=owner,
        updated_by=owner,
    )
    attempt = ScheduledContactAttempt.objects.create(
        plan=plan,
        due_at=timezone.now(),
        state=ScheduledContactAttempt.State.DRAFT_REVIEW,
        idempotency_key="erasure-attempt",
        context_manifest={"person": PERSON},
    )
    return {
        "run": run,
        "prospect": prospect,
        "organization": organization,
        "email": email,
        "contact": contact,
        "sent": sent,
        "pending_reply": pending_reply,
        "inbound": inbound,
        "task": task,
        "plan": plan,
        "attempt": attempt,
    }


@pytest.fixture
def seller(owner: User) -> User:
    return User.objects.create_user(username="seller", password="seller-password-1")


# --- what is erased ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_history_really_contains_the_planted_data(
    owner: User, private_catalog_dir: Path
) -> None:
    """Guards the next test: if nothing were planted, finding nothing would prove nothing."""

    del private_catalog_dir
    _full_history(owner)

    assert set(_leaks()) == set(SENTINELS)


@pytest.mark.django_db
def test_erasure_leaves_nothing_readable_about_the_business_anywhere(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    data = _full_history(owner)

    result = erase_organization(actor=owner, organization_id=data["organization"].pk)

    assert result.already_erased is False
    assert _leaks() == []


@pytest.mark.django_db
def test_erasure_keeps_only_the_address_and_hashes_needed_to_keep_the_block(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    data = _full_history(owner)
    organization = data["organization"]

    erase_organization(actor=owner, organization_id=organization.pk)

    organization.refresh_from_db()
    assert organization.erased_at is not None
    assert (organization.name, organization.address, organization.phone) == ("", "", "")
    assert (organization.website, organization.business_domain) == ("", "")
    email = EmailAddress.objects.get(pk=data["email"].pk)
    assert email.normalized_email == email.original_email == EMAIL
    identities = OrganizationIdentity.objects.filter(organization=organization)
    assert identities.exists()
    assert set(identities.values_list("value", flat=True)) == {""}
    assert all(len(value) == 64 for value in identities.values_list("value_hash", flat=True))
    sent = OutboundMessage.objects.get(pk=data["sent"].pk)
    assert sent.recipient == sent.recipient_normalized == EMAIL
    assert sent.state == OutboundMessage.State.SENT
    assert sent.message_id == "<sent@example.invalid>"
    assert SuppressionEntry.objects.filter(normalized_email=EMAIL).exists()


@pytest.mark.django_db
def test_erasure_keeps_other_businesses_in_the_stored_search_page(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    data = _full_history(owner)

    erase_organization(actor=owner, organization_id=data["organization"].pk)

    data["run"].refresh_from_db()
    assert data["run"].response_json["data"] == [
        {"overture_id": "place-vecino", "name": "Vecino Conservado", "address": "Otra 1"}
    ]
    assert data["run"].response_json["status"] == "SUCCEEDED"


@pytest.mark.django_db
def test_erasure_does_not_touch_another_business(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    data = _full_history(owner)
    other_run = _run(owner, suffix="otro")
    other = ingest_business(
        run=other_run,
        business=ExtractedBusiness(
            provider_id="place-otro",
            name="Ferretería Vecina",
            address="Avenida Real 10",
            email_candidates=(ExtractedEmail(value="hola@ferreteria.example", is_primary=True),),
        ),
        resolver=MockMXResolver(),
    ).prospect
    assert other is not None

    erase_organization(actor=owner, organization_id=data["organization"].pk)

    other.refresh_from_db()
    assert other.name == "Ferretería Vecina"
    assert other.erased_at is None
    assert other.organization is not None
    assert other.organization.erased_at is None
    assert other.organization.name == "Ferretería Vecina"
    assert not SuppressionEntry.objects.filter(normalized_email="hola@ferreteria.example").exists()


# --- what is stopped ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_erasure_cancels_everything_still_waiting_to_happen(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    data = _full_history(owner)

    erase_organization(actor=owner, organization_id=data["organization"].pk)

    pending = OutboundMessage.objects.get(pk=data["pending_reply"].pk)
    assert pending.state == OutboundMessage.State.CANCELLED
    task = HumanTask.objects.get(pk=data["task"].pk)
    assert task.status == HumanTask.Status.DISMISSED
    assert task.resolved_by == owner
    plan = ContactCommunicationPlan.objects.get(pk=data["plan"].pk)
    assert plan.state == ContactCommunicationPlan.State.DISABLED
    assert plan.next_due_at is None
    attempt = ScheduledContactAttempt.objects.get(pk=data["attempt"].pk)
    assert attempt.state == ScheduledContactAttempt.State.CANCELLED
    contact = Contact.objects.get(pk=data["contact"].pk)
    assert contact.status == Contact.Status.ERASED
    assert contact.automation_suspended is True
    prospect = Prospect.objects.get(pk=data["prospect"].pk)
    assert prospect.pipeline_state == Prospect.PipelineState.SKIPPED_DUPLICATE
    assert prospect.erased_at is not None


@pytest.mark.django_db
def test_erasure_is_refused_while_a_message_is_leaving_and_changes_nothing(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    data = _full_history(owner)
    OutboundMessage.objects.filter(pk=data["pending_reply"].pk).update(
        state=OutboundMessage.State.SENDING
    )

    with pytest.raises(ValidationError, match="saliendo"):
        erase_organization(actor=owner, organization_id=data["organization"].pk)

    data["organization"].refresh_from_db()
    assert data["organization"].erased_at is None
    assert data["organization"].name == NAME
    assert not CommunicationRestriction.objects.filter(
        kind=CommunicationRestriction.Kind.ERASURE
    ).exists()
    assert set(_leaks()) == set(SENTINELS)


@pytest.mark.django_db
def test_erasure_is_refused_while_an_automatic_reply_is_executing(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    data = _full_history(owner)
    ReplyDecision.objects.filter(contact=data["contact"]).update(
        state=ReplyDecision.State.EXECUTING
    )

    with pytest.raises(ValidationError, match="saliendo"):
        erase_organization(actor=owner, organization_id=data["organization"].pk)

    data["organization"].refresh_from_db()
    assert data["organization"].erased_at is None


# --- the block that stays ----------------------------------------------------------------------


@pytest.mark.django_db
def test_an_erased_business_is_never_eligible_again(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    data = _full_history(owner)

    erase_organization(actor=owner, organization_id=data["organization"].pk)

    enrollment = CampaignEnrollment.objects.get(organization=data["organization"])
    eligibility = enrollment_eligibility(enrollment)
    assert eligibility.eligible is False
    assert eligibility.code == "ERASED"
    assert enrollment.state == CampaignEnrollment.State.INELIGIBLE
    contact = Contact.objects.get(pk=data["contact"].pk)
    assert communication_is_restricted(contact=contact) is True
    assert communication_is_restricted(contact=contact, email_address=data["email"]) is True


@pytest.mark.django_db
def test_the_erasure_block_can_never_be_lifted(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    data = _full_history(owner)
    erase_organization(actor=owner, organization_id=data["organization"].pk)
    restriction = CommunicationRestriction.objects.get(kind=CommunicationRestriction.Kind.ERASURE)

    with pytest.raises(ValidationError):
        set_contact_no_contact(actor=owner, contact_id=data["contact"].pk, blocked=False)
    with pytest.raises(ValidationError):
        revoke_manual_restriction(actor=owner, restriction_id=restriction.pk, reason="Probar")
    restriction.revoked_at = timezone.now()
    restriction.revoked_by = owner
    restriction.revocation_reason = "Probar"
    with pytest.raises(ValidationError):
        restriction.save()
    with pytest.raises(IntegrityError), transaction.atomic():
        CommunicationRestriction.objects.filter(pk=restriction.pk).update(
            revoked_at=timezone.now(), revoked_by=owner, revocation_reason="Probar"
        )

    restriction.refresh_from_db()
    assert restriction.revoked_at is None


@pytest.mark.django_db
def test_lifting_an_older_manual_block_does_not_remove_the_suppression(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    data = _full_history(owner)
    set_contact_no_contact(actor=owner, contact_id=data["contact"].pk, blocked=True)
    manual = CommunicationRestriction.objects.get(
        contact=data["contact"], kind=CommunicationRestriction.Kind.MANUAL
    )
    entry = SuppressionEntry.objects.get(normalized_email=EMAIL)
    assert entry.source == "contacts_dashboard"

    erase_organization(actor=owner, organization_id=data["organization"].pk)
    revoke_manual_restriction(actor=owner, restriction_id=manual.pk, reason="Ya no aplica")

    entry = SuppressionEntry.objects.get(normalized_email=EMAIL)
    assert entry.source == "organization_erasure"
    enrollment = CampaignEnrollment.objects.get(organization=data["organization"])
    assert enrollment_eligibility(enrollment).eligible is False


@pytest.mark.django_db
def test_an_unsubscribe_stays_an_unsubscribe_after_erasure(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    data = _full_history(owner)
    SuppressionEntry.objects.create(
        original_email=EMAIL,
        normalized_email=EMAIL,
        reason=SuppressionEntry.Reason.UNSUBSCRIBE,
        source="gmail_reply",
        evidence="InboundMessage:1",
    )

    erase_organization(actor=owner, organization_id=data["organization"].pk)

    entry = SuppressionEntry.objects.get(normalized_email=EMAIL)
    assert entry.reason == SuppressionEntry.Reason.UNSUBSCRIBE
    assert entry.source == "gmail_reply"


@pytest.mark.django_db
def test_a_business_that_was_only_discovered_is_blocked_too(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    _, prospect = _discover(owner)
    organization = Organization.objects.get(pk=prospect.organization_id)
    assert not Contact.objects.filter(organization=organization).exists()

    erase_organization(actor=owner, organization_id=organization.pk)

    contact = Contact.objects.get(organization=organization)
    assert contact.created_reason == Contact.CreatedReason.ERASURE
    assert contact.status == Contact.Status.ERASED
    assert CommunicationRestriction.objects.filter(
        contact=contact, kind=CommunicationRestriction.Kind.ERASURE, revoked_at__isnull=True
    ).exists()
    assert SuppressionEntry.objects.filter(normalized_email=EMAIL).exists()
    assert _leaks() == []


# --- finding it again --------------------------------------------------------------------------


@pytest.mark.django_db
def test_rediscovering_an_erased_business_stores_nothing_about_it(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    data = _full_history(owner)
    erase_organization(actor=owner, organization_id=data["organization"].pk)

    later = _run(owner, suffix="dos")
    result = ingest_business(run=later, business=_business(), resolver=MockMXResolver())

    assert result.outcome == IngestOutcome.DUPLICATE
    assert result.prospect is not None
    assert result.prospect.pk == data["prospect"].pk
    assert not Prospect.objects.filter(campaign=later.campaign).exists()
    assert _leaks() == []


@pytest.mark.django_db
def test_a_new_address_found_for_an_erased_business_is_not_stored(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    data = _full_history(owner)
    erase_organization(actor=owner, organization_id=data["organization"].pk)
    new_address = "compras@zafiro-nuevo.example"

    later = _run(owner, suffix="cinco")
    result = ingest_business(
        run=later,
        business=ExtractedBusiness(
            provider_id=PROVIDER_ID,
            name=NAME,
            address=ADDRESS,
            email_candidates=(
                ExtractedEmail(value=EMAIL, is_primary=True),
                ExtractedEmail(value=new_address, order=1),
            ),
        ),
        resolver=MockMXResolver(),
    )

    assert result.outcome == IngestOutcome.DUPLICATE
    assert not EmailAddress.objects.filter(normalized_email=new_address).exists()
    assert _leaks() == []


@pytest.mark.django_db
def test_rediscovering_it_under_a_new_catalog_id_is_still_recognised(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    data = _full_history(owner)
    erase_organization(actor=owner, organization_id=data["organization"].pk)

    later = _run(owner, suffix="tres")
    result = ingest_business(
        run=later, business=_business(provider_id="place-nuevo"), resolver=MockMXResolver()
    )

    assert result.outcome == IngestOutcome.DUPLICATE
    assert Organization.objects.count() == 1
    assert _leaks() == []


@pytest.mark.django_db
def test_a_new_record_with_the_same_name_and_address_is_erased_on_arrival(
    owner: User, private_catalog_dir: Path
) -> None:
    """A different email and catalog id, but the same place: recognised by its identity hash."""

    del private_catalog_dir
    data = _full_history(owner)
    erase_organization(actor=owner, organization_id=data["organization"].pk)
    # The old prospect no longer shares a hash with the new record, so only the organization's
    # NAME_ADDRESS identity can recognise it.
    Prospect.objects.filter(pk=data["prospect"].pk).update(organization=None)
    data["prospect"].identities.all().delete()

    later = _run(owner, suffix="cuatro")
    result = ingest_business(
        run=later,
        business=ExtractedBusiness(
            provider_id="place-cuatro",
            name=NAME,
            address=ADDRESS,
            phone=PHONE,
            email_candidates=(
                ExtractedEmail(value="otro@correo-negocio.example", is_primary=True),
            ),
        ),
        resolver=MockMXResolver(),
    )

    assert result.prospect is not None
    arrived = Prospect.objects.get(pk=result.prospect.pk)
    assert arrived.organization_id == data["organization"].pk
    assert arrived.erased_at is not None
    assert arrived.pipeline_state == Prospect.PipelineState.SKIPPED_DUPLICATE
    assert not EmailAddress.objects.filter(normalized_email="otro@correo-negocio.example").exists()
    assert _leaks() == []


@pytest.mark.django_db
def test_the_raw_search_page_drops_an_erased_business_before_it_is_stored(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    data = _full_history(owner)
    erase_organization(actor=owner, organization_id=data["organization"].pk)
    page = {
        "status": "SUCCEEDED",
        "data": [
            {"overture_id": PROVIDER_ID, "name": NAME},
            {"overture_id": "place-libre", "name": "Negocio Libre"},
        ],
    }

    kept = drop_erased_rows("fake", page)

    assert kept["data"] == [{"overture_id": "place-libre", "name": "Negocio Libre"}]
    assert kept["status"] == "SUCCEEDED"
    assert page["data"][0]["name"] == NAME  # the caller's page is not mutated
    assert drop_erased_rows("otro-proveedor", page) == page


@pytest.mark.django_db
def test_work_already_running_for_the_business_cannot_write_its_data_back(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    _, prospect = _discover(owner)
    erase_organization(actor=owner, organization_id=prospect.organization_id)

    with pytest.raises(ProspectPipelineInactive):
        _persist_enrichment(
            prospect_id=prospect.pk,
            requested_url=WEBSITE,
            page_rows=[],
            email_candidate_rows=[],
            excerpt=NAME,
            error="",
            error_kind=None,
        )

    assert not WebsiteSnapshot.objects.filter(prospect=prospect).exists()
    assert _leaks() == []


# --- who and how often -------------------------------------------------------------------------


@pytest.mark.django_db
def test_erasing_twice_is_harmless_and_audited_once(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    data = _full_history(owner)

    first = erase_organization(actor=owner, organization_id=data["organization"].pk)
    second = erase_organization(actor=owner, organization_id=data["organization"].pk)

    assert first.already_erased is False
    assert second.already_erased is True
    assert second.organization.erased_at == first.organization.erased_at
    events = AuditEvent.objects.filter(action="organization.erased")
    assert events.count() == 1
    assert events.get().actor == owner
    assert (
        CommunicationRestriction.objects.filter(kind=CommunicationRestriction.Kind.ERASURE).count()
        == 1
    )


@pytest.mark.django_db
def test_a_seller_cannot_erase(owner: User, seller: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    data = _full_history(owner)

    with pytest.raises(PermissionDenied):
        erase_organization(actor=seller, organization_id=data["organization"].pk)

    data["organization"].refresh_from_db()
    assert data["organization"].erased_at is None


# --- API ---------------------------------------------------------------------------------------


def _csrf_token(client: Client) -> str:
    return str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])


def _post(client: Client, url: str, payload: dict[str, str], csrf: str) -> Any:
    return client.post(
        url, data=json.dumps(payload), content_type="application/json", HTTP_X_CSRFTOKEN=csrf
    )


def _reauthenticated_client(user: User, password: str) -> tuple[Client, str]:
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    csrf = _csrf_token(client)
    response = _post(client, reverse("api-auth-reauthenticate"), {"password": password}, csrf)
    assert response.status_code == 200
    return client, csrf


@pytest.mark.django_db
def test_the_api_requires_a_fresh_password_and_the_confirmation_word(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    data = _full_history(owner)
    url = reverse("api-contact-erase", args=(data["contact"].pk,))

    stale = Client(enforce_csrf_checks=True)
    stale.force_login(owner)
    denied = _post(stale, url, {"confirmation": "ELIMINAR"}, _csrf_token(stale))
    assert denied.status_code == 403

    client, csrf = _reauthenticated_client(owner, "correct-password")
    wrong_word = _post(client, url, {"confirmation": "eliminar"}, csrf)
    assert wrong_word.status_code == 400
    data["organization"].refresh_from_db()
    assert data["organization"].erased_at is None

    done = _post(client, url, {"confirmation": "ELIMINAR"}, csrf)
    assert done.status_code == 200, done.content
    assert done.json()["data"]["already_erased"] is False
    assert done.json()["data"]["organization_id"] == str(data["organization"].pk)
    assert _leaks() == []

    again = _post(client, url, {"confirmation": "ELIMINAR"}, csrf)
    assert again.status_code == 200
    assert again.json()["data"]["already_erased"] is True


@pytest.mark.django_db
def test_the_api_refuses_a_seller(owner: User, seller: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    data = _full_history(owner)
    client, csrf = _reauthenticated_client(seller, "seller-password-1")

    response = _post(
        client,
        reverse("api-contact-erase", args=(data["contact"].pk,)),
        {"confirmation": "ELIMINAR"},
        csrf,
    )

    assert response.status_code == 403
    data["organization"].refresh_from_db()
    assert data["organization"].erased_at is None


@pytest.mark.django_db
def test_the_api_erases_a_business_from_the_search_results(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    _, prospect = _discover(owner)
    client, csrf = _reauthenticated_client(owner, "correct-password")

    missing = _post(
        client,
        reverse("api-prospect-erase", args=("00000000-0000-0000-0000-000000000000",)),
        {"confirmation": "ELIMINAR"},
        csrf,
    )
    assert missing.status_code == 404

    done = _post(
        client,
        reverse("api-prospect-erase", args=(prospect.pk,)),
        {"confirmation": "ELIMINAR"},
        csrf,
    )

    assert done.status_code == 200, done.content
    assert Organization.objects.get(pk=prospect.organization_id).erased_at is not None
    assert _leaks() == []


@pytest.mark.django_db
def test_the_api_reports_a_message_leaving_as_a_conflict_to_retry(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    data = _full_history(owner)
    OutboundMessage.objects.filter(pk=data["pending_reply"].pk).update(
        state=OutboundMessage.State.RECONCILING
    )
    client, csrf = _reauthenticated_client(owner, "correct-password")

    response = _post(
        client,
        reverse("api-contact-erase", args=(data["contact"].pk,)),
        {"confirmation": "ELIMINAR"},
        csrf,
    )

    assert response.status_code == 400
    assert "saliendo" in response.json()["detail"]
    data["organization"].refresh_from_db()
    assert data["organization"].erased_at is None
