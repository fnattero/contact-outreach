from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.automation.models import ContactCommunicationPlan, FollowUpTopic, ScheduledContactAttempt
from apps.campaigns.models import Campaign, OutboundMessage
from apps.contacts.models import Contact, EmailAddress
from apps.contacts.services import create_manual_contact
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import GmailConnection


class _Api:
    """An authenticated, CSRF-enforcing client with JSON helpers."""

    def __init__(self, user: User) -> None:
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(user)
        self.csrf = str(self.client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])

    def send(self, method: str, url: str, payload: dict[str, Any] | None = None):
        return getattr(self.client, method)(
            url,
            data=json.dumps(payload or {}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.csrf,
        )


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


def _follow_up_topic(owner: User, *, suffix: str) -> FollowUpTopic:
    return FollowUpTopic.objects.create(
        workspace=owner.membership.workspace,
        name=f"Tema {suffix}",
        objective="Retomar el contacto de manera cordial.",
        cadence_days=30,
        mode=FollowUpTopic.Mode.REVIEW_BEFORE_SEND,
        next_due_at=timezone.now() + timedelta(days=10),
        active=True,
        created_by=owner,
        updated_by=owner,
    )


def _draft_attempt(
    contact: Contact,
    email: EmailAddress,
    *,
    suffix: str,
    attempt_state: str = ScheduledContactAttempt.State.DRAFT_REVIEW,
    with_outbound: bool = True,
) -> tuple[ContactCommunicationPlan, ScheduledContactAttempt]:
    actor = contact.created_by
    assert isinstance(actor, User)
    plan = ContactCommunicationPlan.objects.create(
        contact=contact,
        topic=_follow_up_topic(actor, suffix=suffix),
        preferred_email=email,
        state=ContactCommunicationPlan.State.ACTIVE,
        next_due_at=timezone.now(),
        created_by=actor,
        updated_by=actor,
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
            idempotency_key=f"scheduled-test-{suffix}",
        )
    attempt = ScheduledContactAttempt.objects.create(
        plan=plan,
        due_at=timezone.now(),
        outbound_message=outbound,
        state=attempt_state,
        idempotency_key=f"scheduled-attempt-{suffix}",
    )
    return plan, attempt


def _urls(contact: Contact, attempt: ScheduledContactAttempt) -> tuple[str, str]:
    return (
        reverse("api-scheduled-attempt-draft", args=(contact.pk, attempt.pk)),
        reverse("api-scheduled-attempt-authorize", args=(contact.pk, attempt.pk)),
    )


@pytest.mark.django_db
def test_admin_edits_a_scheduled_draft_until_it_stops_being_editable(owner: User) -> None:
    contact, email = _validated_contact(owner, suffix="borrador")
    _, attempt = _draft_attempt(contact, email, suffix="edit")
    other_contact, _ = _validated_contact(owner, suffix="otro-borrador")
    api = _Api(owner)
    draft_url, _ = _urls(contact, attempt)

    edited = api.send("patch", draft_url, {"subject": "Nuevo asunto", "body_text": "Nuevo cuerpo"})

    assert edited.status_code == 200, edited.content
    assert edited.json()["data"]["subject"] == "Nuevo asunto"
    assert attempt.outbound_message is not None
    attempt.outbound_message.refresh_from_db()
    assert attempt.outbound_message.body_text == "Nuevo cuerpo"
    assert attempt.outbound_message.content_revision == 2

    blank = api.send("patch", draft_url, {"subject": "", "body_text": ""})
    assert blank.status_code == 400

    # The service refuses a blank draft with its own message, which must reach the client.
    whitespace = api.send("patch", draft_url, {"subject": " ", "body_text": " "})
    assert whitespace.status_code == 400

    foreign = api.send(
        "patch",
        reverse("api-scheduled-attempt-draft", args=(other_contact.pk, attempt.pk)),
        {"subject": "Otro asunto", "body_text": "Otro cuerpo"},
    )
    assert foreign.status_code == 404

    third, third_email = _validated_contact(owner, suffix="no-editable")
    _, not_editable = _draft_attempt(
        third,
        third_email,
        suffix="no-editable",
        attempt_state=ScheduledContactAttempt.State.DUE,
        with_outbound=False,
    )
    refused = api.send(
        "patch",
        _urls(third, not_editable)[0],
        {"subject": "Asunto", "body_text": "Cuerpo"},
    )
    assert refused.status_code == 400
    assert "ya no se puede editar" in refused.json()["detail"]


@pytest.mark.django_db
def test_admin_authorizes_a_ready_draft_once_gmail_and_switches_allow_it(
    owner: User, settings: Any
) -> None:
    settings.RELATIONSHIP_KILL_SWITCH = False
    settings.SEND_KILL_SWITCH = False
    settings.SEND_MODE = "live"
    contact, email = _validated_contact(owner, suffix="autorizar")
    GmailConnection.objects.create(
        workspace=owner.membership.workspace,
        owner=owner,
        email="equipo@example.invalid",
        scopes=list(GMAIL_SCOPES),
        refresh_token_encrypted=encrypt_token("refresh-token"),
        status=GmailConnection.Status.CONNECTED,
        last_tested_at=timezone.now(),
    )
    _, attempt = _draft_attempt(contact, email, suffix="authorize")
    other_contact, _ = _validated_contact(owner, suffix="otro-autorizar")
    api = _Api(owner)
    _, authorize_url = _urls(contact, attempt)

    foreign = api.send(
        "post", reverse("api-scheduled-attempt-authorize", args=(other_contact.pk, attempt.pk))
    )
    assert foreign.status_code == 404

    authorized = api.send("post", authorize_url)

    assert authorized.status_code == 200, authorized.content
    attempt.refresh_from_db()
    assert attempt.state == ScheduledContactAttempt.State.AUTHORIZED
    assert attempt.outbound_message is not None
    attempt.outbound_message.refresh_from_db()
    assert attempt.outbound_message.state == OutboundMessage.State.QUEUED

    repeated = api.send("post", authorize_url)
    assert repeated.status_code == 400
    assert "ya fue autorizado" in repeated.json()["detail"]


@pytest.mark.django_db
def test_each_attempt_url_serves_only_its_own_method(owner: User) -> None:
    # Both routes used to share one view, so POST /draft/ ran the authorize action.
    contact, email = _validated_contact(owner, suffix="metodos")
    _, attempt = _draft_attempt(contact, email, suffix="methods")
    api = _Api(owner)
    draft_url, authorize_url = _urls(contact, attempt)

    assert api.send("post", draft_url).status_code == 405
    assert api.send("patch", authorize_url, {"subject": "x", "body_text": "y"}).status_code == 405
    attempt.refresh_from_db()
    assert attempt.state == ScheduledContactAttempt.State.DRAFT_REVIEW


@pytest.mark.django_db
def test_vendedor_cannot_edit_or_authorize_scheduled_drafts(owner: User) -> None:
    contact, email = _validated_contact(owner, suffix="vendedor")
    _, attempt = _draft_attempt(contact, email, suffix="vendedor")
    seller = User.objects.create_user(username="seller-plan", password="password-for-tests-9")
    api = _Api(seller)
    draft_url, authorize_url = _urls(contact, attempt)

    assert api.send("patch", draft_url, {"subject": "x", "body_text": "y"}).status_code == 403
    assert api.send("post", authorize_url).status_code == 403
    assert Client().patch(draft_url).status_code == 401


@pytest.mark.django_db
def test_plan_state_changes_and_snooze_through_the_api(owner: User) -> None:
    contact, email = _validated_contact(owner, suffix="plan")
    plan, _ = _draft_attempt(contact, email, suffix="plan")
    api = _Api(owner)

    def action(name: str, payload: dict[str, Any]):
        return api.send(
            "post",
            reverse("api-contact-communication-plan-action", args=(contact.pk, plan.pk, name)),
            payload,
        )

    invalid = action("state", {"state": "NO_EXISTE"})
    assert invalid.status_code == 400

    for state in (
        ContactCommunicationPlan.State.PAUSED,
        ContactCommunicationPlan.State.ACTIVE,
        ContactCommunicationPlan.State.DISABLED,
    ):
        changed = action("state", {"state": state})
        assert changed.status_code == 200, changed.content
        plan.refresh_from_db()
        assert plan.state == state

    plan.state = ContactCommunicationPlan.State.ACTIVE
    plan.save(update_fields=("state", "updated_at"))
    past = (timezone.now() - timedelta(days=1)).isoformat()
    assert action("snooze", {"until": past}).status_code == 400
    assert action("snooze", {}).status_code == 400

    future = (timezone.now() + timedelta(days=10)).isoformat()
    snoozed = action("snooze", {"until": future})
    assert snoozed.status_code == 200, snoozed.content
    plan.refresh_from_db()
    assert plan.snoozed_until is not None


@pytest.mark.django_db
def test_plan_actions_are_scoped_to_the_contact_and_admin_only(owner: User) -> None:
    contact, email = _validated_contact(owner, suffix="alcance")
    plan, _ = _draft_attempt(contact, email, suffix="scope")
    other, _ = _validated_contact(owner, suffix="otro-alcance")
    admin = _Api(owner)
    seller = _Api(
        User.objects.create_user(username="seller-scope", password="password-for-tests-8")
    )

    wrong_contact = admin.send(
        "post",
        reverse("api-contact-communication-plan-action", args=(other.pk, plan.pk, "state")),
        {"state": "PAUSED"},
    )
    assert wrong_contact.status_code == 404

    denied = seller.send(
        "post",
        reverse("api-contact-communication-plan-action", args=(contact.pk, plan.pk, "state")),
        {"state": "PAUSED"},
    )
    assert denied.status_code == 403


@pytest.mark.django_db
def test_preferred_email_changes_and_rejects_an_invalid_address(owner: User) -> None:
    contact, primary = _validated_contact(owner, suffix="preferido")
    workspace = owner.membership.workspace
    secondary = EmailAddress.objects.create(
        workspace=workspace,
        organization=contact.organization,
        original_email="secundario@cliente.example",
        normalized_email="secundario@cliente.example",
        domain="cliente.example",
        validity=EmailAddress.Validity.VALID,
        validated_at=timezone.now(),
    )
    invalid = EmailAddress.objects.create(
        workspace=workspace,
        organization=contact.organization,
        original_email="invalido@cliente.example",
        normalized_email="invalido@cliente.example",
        domain="cliente.example",
        validity=EmailAddress.Validity.INVALID,
        invalid_reason="El dominio no recibe correo.",
    )
    api = _Api(owner)

    changed = api.send(
        "patch", reverse("api-contact-email-preferred", args=(contact.pk, secondary.pk))
    )
    assert changed.status_code == 200, changed.content
    contact.refresh_from_db()
    primary.refresh_from_db()
    secondary.refresh_from_db()
    assert contact.preferred_email == secondary
    assert secondary.is_preferred is True
    assert primary.is_preferred is False

    rejected = api.send(
        "patch", reverse("api-contact-email-preferred", args=(contact.pk, invalid.pk))
    )
    assert rejected.status_code == 400
    contact.refresh_from_db()
    assert contact.preferred_email == secondary
