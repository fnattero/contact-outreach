from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.automation.models import ContactCommunicationPlan, ScheduledContactAttempt
from apps.campaigns.models import Campaign, OutboundMessage
from apps.contacts.models import Contact, EmailAddress
from apps.contacts.services import create_manual_contact
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import GmailConnection


def _validated_contact(owner: User, *, suffix: str) -> tuple[Contact, EmailAddress]:
    contact = create_manual_contact(
        actor=owner,
        email=f"contacto-{suffix}@cliente.example",
        organization_name=f"Cliente {suffix}",
        contact_name="Persona de contacto",
    )
    email = contact.preferred_email
    assert email is not None
    email.validity = EmailAddress.Validity.VALID
    email.validated_at = timezone.now()
    email.save(update_fields=("validity", "validated_at", "updated_at"))
    return contact, email


def _ready_gmail_connection(owner: User) -> GmailConnection:
    return GmailConnection.objects.create(
        workspace=owner.membership.workspace,
        owner=owner,
        email="equipo@example.com",
        scopes=list(GMAIL_SCOPES),
        refresh_token_encrypted=encrypt_token("refresh-token"),
        status=GmailConnection.Status.CONNECTED,
        last_tested_at=timezone.now(),
    )


def _messages(response) -> list[str]:
    return [str(item) for item in get_messages(response.wsgi_request)]


@pytest.mark.django_db
def test_admin_can_save_activate_pause_and_snooze_the_communication_plan(
    client: Client,
    owner: User,
) -> None:
    contact, email = _validated_contact(owner, suffix="plan")
    other_email = EmailAddress.objects.create(
        workspace=owner.membership.workspace,
        organization=contact.organization,
        original_email="otro-canal@cliente.example",
        normalized_email="otro-canal@cliente.example",
        domain="cliente.example",
        validity=EmailAddress.Validity.VALID,
        validated_at=timezone.now(),
    )
    client.force_login(owner)
    save_url = reverse("contact-plan-save", args=(contact.pk,))
    future = timezone.now() + timedelta(days=10)

    saved = client.post(
        save_url,
        {
            "enabled": "on",
            "preferred_email": str(email.pk),
            "purpose": ContactCommunicationPlan.Purpose.CHECK_IN,
            "goal_text": "",
            "cadence_days": "30",
            "mode": ContactCommunicationPlan.Mode.REVIEW_BEFORE_SEND,
            "next_due_at": future.strftime("%Y-%m-%dT%H:%M"),
        },
    )
    assert saved.status_code == 302
    plan = ContactCommunicationPlan.objects.get(contact=contact)
    assert plan.state == ContactCommunicationPlan.State.ACTIVE
    assert plan.preferred_email == email

    # Invalid form: cadence below the seven day minimum.
    invalid = client.post(
        save_url,
        {
            "enabled": "on",
            "preferred_email": str(email.pk),
            "purpose": ContactCommunicationPlan.Purpose.CHECK_IN,
            "cadence_days": "1",
            "mode": ContactCommunicationPlan.Mode.REVIEW_BEFORE_SEND,
        },
    )
    assert invalid.status_code == 302
    assert _messages(invalid)
    plan.refresh_from_db()
    assert plan.cadence_days == 30

    # Service-level validation error: chosen email is not the contact's preferred email.
    conflict = client.post(
        save_url,
        {
            "enabled": "on",
            "preferred_email": str(other_email.pk),
            "purpose": ContactCommunicationPlan.Purpose.CHECK_IN,
            "cadence_days": "30",
            "mode": ContactCommunicationPlan.Mode.REVIEW_BEFORE_SEND,
        },
    )
    assert conflict.status_code == 302
    assert any("Elegí primero este email" in m for m in _messages(conflict))
    plan.refresh_from_db()
    assert plan.preferred_email == email

    # Plan state transitions.
    no_plan_contact, _ = _validated_contact(owner, suffix="sinplan")
    missing_plan = client.post(
        reverse("contact-plan-state", args=(no_plan_contact.pk, "activar")),
    )
    assert missing_plan.status_code == 302
    assert any("Primero configurá" in m for m in _messages(missing_plan))

    invalid_state = client.post(
        reverse("contact-plan-state", args=(contact.pk, "no-existe")),
    )
    assert invalid_state.status_code == 302
    assert any("no es válida" in m for m in _messages(invalid_state))

    paused = client.post(reverse("contact-plan-state", args=(contact.pk, "pausar")))
    assert paused.status_code == 302
    plan.refresh_from_db()
    assert plan.state == ContactCommunicationPlan.State.PAUSED

    reactivated = client.post(reverse("contact-plan-state", args=(contact.pk, "activar")))
    assert reactivated.status_code == 302
    plan.refresh_from_db()
    assert plan.state == ContactCommunicationPlan.State.ACTIVE

    disabled = client.post(reverse("contact-plan-state", args=(contact.pk, "desactivar")))
    assert disabled.status_code == 302
    plan.refresh_from_db()
    assert plan.state == ContactCommunicationPlan.State.DISABLED

    # Snooze: missing plan, invalid form, business-rule error, and success.
    snooze_url_missing_plan = reverse("contact-plan-snooze", args=(no_plan_contact.pk,))
    missing_plan_snooze = client.post(
        snooze_url_missing_plan,
        {"until": future.strftime("%Y-%m-%dT%H:%M")},
    )
    assert missing_plan_snooze.status_code == 302
    assert any("Primero configurá" in m for m in _messages(missing_plan_snooze))

    plan.state = ContactCommunicationPlan.State.ACTIVE
    plan.save(update_fields=("state", "updated_at"))
    snooze_url = reverse("contact-plan-snooze", args=(contact.pk,))
    invalid_snooze = client.post(snooze_url, {})
    assert invalid_snooze.status_code == 302
    assert any("Elegí una fecha futura" in m for m in _messages(invalid_snooze))

    past = timezone.now() - timedelta(days=1)
    past_snooze = client.post(snooze_url, {"until": past.strftime("%Y-%m-%dT%H:%M")})
    assert past_snooze.status_code == 302
    assert any("fecha futura" in m for m in _messages(past_snooze))

    good_snooze = client.post(snooze_url, {"until": future.strftime("%Y-%m-%dT%H:%M")})
    assert good_snooze.status_code == 302
    assert any("pospuesto" in m for m in _messages(good_snooze))
    plan.refresh_from_db()
    assert plan.snoozed_until is not None


def _draft_attempt(
    contact: Contact,
    email: EmailAddress,
    *,
    plan_state: str = ContactCommunicationPlan.State.ACTIVE,
    attempt_state: str = ScheduledContactAttempt.State.DRAFT_REVIEW,
    with_outbound: bool = True,
    idempotency_suffix: str = "one",
) -> tuple[ContactCommunicationPlan, ScheduledContactAttempt]:
    plan = ContactCommunicationPlan.objects.create(
        contact=contact,
        preferred_email=email,
        purpose=ContactCommunicationPlan.Purpose.CHECK_IN,
        cadence_days=30,
        mode=ContactCommunicationPlan.Mode.REVIEW_BEFORE_SEND,
        state=plan_state,
        next_due_at=timezone.now(),
        created_by=contact.created_by,
        updated_by=contact.created_by,
    )
    outbound = None
    if with_outbound:
        outbound = OutboundMessage.objects.create(
            kind=OutboundMessage.Kind.SCHEDULED_CONTACT,
            organization=contact.organization,
            contact=contact,
            email_address=email,
            recipient=email.original_email,
            recipient_normalized=email.normalized_email,
            subject="Asunto original",
            body_text="Cuerpo original",
            state=OutboundMessage.State.REVIEW_READY,
            delivery_mode=Campaign.DeliveryMode.LIVE,
            idempotency_key=f"scheduled-test-{idempotency_suffix}",
        )
    attempt = ScheduledContactAttempt.objects.create(
        plan=plan,
        due_at=timezone.now(),
        outbound_message=outbound,
        state=attempt_state,
        idempotency_key=f"scheduled-attempt-{idempotency_suffix}",
    )
    return plan, attempt


@pytest.mark.django_db
def test_admin_can_edit_a_scheduled_draft_but_not_after_it_stops_being_editable(
    client: Client,
    owner: User,
) -> None:
    contact, email = _validated_contact(owner, suffix="borrador")
    _plan, attempt = _draft_attempt(contact, email, idempotency_suffix="edit")
    other_contact, _other_email = _validated_contact(owner, suffix="otro-borrador")
    client.force_login(owner)
    edit_url = reverse(
        "scheduled-contact-draft-edit",
        args=(contact.pk, attempt.pk),
    )

    edited = client.post(edit_url, {"subject": "Nuevo asunto", "body_text": "Nuevo cuerpo"})
    assert edited.status_code == 302
    assert any("Borrador guardado" in m for m in _messages(edited))
    attempt.outbound_message.refresh_from_db()
    assert attempt.outbound_message.subject == "Nuevo asunto"
    assert attempt.outbound_message.body_text == "Nuevo cuerpo"
    assert attempt.outbound_message.content_revision == 2

    invalid = client.post(edit_url, {"subject": "", "body_text": ""})
    assert invalid.status_code == 302
    assert any("Completá el asunto" in m for m in _messages(invalid))

    mismatched = client.post(
        reverse("scheduled-contact-draft-edit", args=(other_contact.pk, attempt.pk)),
        {"subject": "Otro asunto", "body_text": "Otro cuerpo"},
    )
    assert mismatched.status_code == 404

    third_contact, third_email = _validated_contact(owner, suffix="no-editable")
    _plan2, not_editable = _draft_attempt(
        third_contact,
        third_email,
        attempt_state=ScheduledContactAttempt.State.DUE,
        with_outbound=False,
        idempotency_suffix="no-editable",
    )
    not_editable_response = client.post(
        reverse("scheduled-contact-draft-edit", args=(third_contact.pk, not_editable.pk)),
        {"subject": "Asunto", "body_text": "Cuerpo"},
    )
    assert not_editable_response.status_code == 302
    assert any("ya no se puede editar" in m for m in _messages(not_editable_response))


@pytest.mark.django_db
def test_admin_can_authorize_a_ready_scheduled_draft_once_gmail_and_switches_allow_it(
    client: Client,
    owner: User,
    settings: pytest.FixtureRequest,
) -> None:
    settings.RELATIONSHIP_KILL_SWITCH = False
    settings.SEND_KILL_SWITCH = False
    settings.SEND_MODE = "live"
    contact, email = _validated_contact(owner, suffix="autorizar")
    _ready_gmail_connection(owner)
    _plan, attempt = _draft_attempt(contact, email, idempotency_suffix="authorize")
    other_contact, _other_email = _validated_contact(owner, suffix="otro-autorizar")
    client.force_login(owner)
    authorize_url = reverse(
        "scheduled-contact-authorize",
        args=(contact.pk, attempt.pk),
    )

    mismatched = client.post(
        reverse("scheduled-contact-authorize", args=(other_contact.pk, attempt.pk)),
    )
    assert mismatched.status_code == 404

    authorized = client.post(authorize_url)
    assert authorized.status_code == 302
    assert any("autorizado" in m for m in _messages(authorized))
    attempt.refresh_from_db()
    attempt.outbound_message.refresh_from_db()
    assert attempt.state == ScheduledContactAttempt.State.AUTHORIZED
    assert attempt.outbound_message.state == OutboundMessage.State.QUEUED

    third_contact, third_email = _validated_contact(owner, suffix="no-autorizable")
    _plan2, not_authorizable = _draft_attempt(
        third_contact,
        third_email,
        attempt_state=ScheduledContactAttempt.State.DUE,
        with_outbound=False,
        idempotency_suffix="no-authorizable",
    )
    blocked = client.post(
        reverse("scheduled-contact-authorize", args=(third_contact.pk, not_authorizable.pk)),
    )
    assert blocked.status_code == 302
    assert any("ya fue autorizado" in m for m in _messages(blocked))


@pytest.mark.django_db
def test_admin_can_change_the_preferred_email_and_sees_the_validation_error(
    client: Client,
    owner: User,
) -> None:
    contact, primary = _validated_contact(owner, suffix="preferido")
    secondary = EmailAddress.objects.create(
        workspace=owner.membership.workspace,
        organization=contact.organization,
        original_email="secundario@cliente.example",
        normalized_email="secundario@cliente.example",
        domain="cliente.example",
        validity=EmailAddress.Validity.VALID,
        validated_at=timezone.now(),
    )
    invalid_email = EmailAddress.objects.create(
        workspace=owner.membership.workspace,
        organization=contact.organization,
        original_email="invalido@cliente.example",
        normalized_email="invalido@cliente.example",
        domain="cliente.example",
        validity=EmailAddress.Validity.INVALID,
        invalid_reason="El dominio no recibe correo.",
    )
    client.force_login(owner)

    changed = client.post(
        reverse("contact-email-preferred", args=(contact.pk, secondary.pk)),
    )
    assert changed.status_code == 302
    assert any("preferido actualizado" in m for m in _messages(changed))
    contact.refresh_from_db()
    primary.refresh_from_db()
    secondary.refresh_from_db()
    assert contact.preferred_email == secondary
    assert secondary.is_preferred is True
    assert primary.is_preferred is False

    rejected = client.post(
        reverse("contact-email-preferred", args=(contact.pk, invalid_email.pk)),
    )
    assert rejected.status_code == 302
    assert any("no puede ser el email preferido" in m for m in _messages(rejected))
    contact.refresh_from_db()
    assert contact.preferred_email == secondary


@pytest.mark.django_db
def test_vendedor_cannot_manage_communication_plans_scheduled_drafts_or_preferred_email(
    client: Client,
    owner: User,
) -> None:
    contact, email = _validated_contact(owner, suffix="vendedor")
    _plan, attempt = _draft_attempt(contact, email, idempotency_suffix="vendedor")
    seller = User.objects.create_user(username="seller-plan", password="password")
    client.force_login(seller)

    forbidden = (
        (reverse("contact-plan-save", args=(contact.pk,)), {}),
        (reverse("contact-plan-state", args=(contact.pk, "activar")), {}),
        (reverse("contact-plan-snooze", args=(contact.pk,)), {}),
        (
            reverse("scheduled-contact-draft-edit", args=(contact.pk, attempt.pk)),
            {"subject": "x", "body_text": "y"},
        ),
        (reverse("scheduled-contact-authorize", args=(contact.pk, attempt.pk)), {}),
        (reverse("contact-email-preferred", args=(contact.pk, email.pk)), {}),
    )
    for url, payload in forbidden:
        assert client.post(url, payload).status_code == 403
