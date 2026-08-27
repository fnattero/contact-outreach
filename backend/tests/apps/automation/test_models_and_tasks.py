from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from apps.automation.models import (
    HumanTask,
    KnowledgeFactRevision,
    NotificationDelivery,
    ReplyAutomationConfiguration,
)
from apps.automation.services import (
    _human_task_lock_queryset,
    approve_knowledge_revision,
    close_human_task,
    create_knowledge_revision,
    open_human_task,
)
from apps.contacts.models import Contact, Conversation, EmailAddress, Organization
from apps.mailbox.models import GmailConnection


@pytest.mark.django_db
def test_knowledge_revisions_require_explicit_approval_and_are_immutable(owner) -> None:
    workspace = owner.membership.workspace
    first = create_knowledge_revision(
        workspace=workspace,
        actor=owner,
        title="Experiencia",
        category="Empresa",
        text="Trabajamos desde hace veinte años.",
    )
    assert not first.is_approved

    approved = approve_knowledge_revision(first, actor=owner)
    assert approved.is_approved
    approved.text = "Texto cambiado"
    with pytest.raises(ValidationError, match="no se edita"):
        approved.save()

    second = create_knowledge_revision(
        workspace=workspace,
        actor=owner,
        title="Experiencia",
        category="Empresa",
        text="Trabajamos desde hace veintiún años.",
    )
    approve_knowledge_revision(second, actor=owner)
    first.refresh_from_db()
    assert first.superseded_at is not None


def _contact_conversation(owner):
    workspace = owner.membership.workspace
    organization = Organization.objects.create(workspace=workspace, name="Cliente")
    email = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email="cliente@example.com",
        normalized_email="cliente@example.com",
        validity=EmailAddress.Validity.VALID,
        is_preferred=True,
    )
    contact = Contact.objects.create(
        workspace=workspace,
        organization=organization,
        preferred_email=email,
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
    )
    connection = GmailConnection.objects.create(
        workspace=workspace,
        owner=owner,
        email="sender@example.com",
        status=GmailConnection.Status.CONNECTED,
    )
    conversation = Conversation.objects.create(
        workspace=workspace,
        contact=contact,
        connection=connection,
        gmail_thread_id="thread-1",
    )
    return contact, conversation


@pytest.mark.django_db
@override_settings(PUBLIC_BASE_URL="https://outreach.example")
def test_human_task_suspends_conversation_and_creates_only_generic_alert(
    owner,
    django_capture_on_commit_callbacks,
) -> None:
    owner.email = "admin@example.com"
    owner.save(update_fields=("email",))
    contact, conversation = _contact_conversation(owner)
    with django_capture_on_commit_callbacks(execute=True):
        task = open_human_task(
            workspace=contact.workspace,
            contact_id=contact.pk,
            conversation_id=conversation.pk,
            kind="REPLY_REVIEW",
            reason="MEETING_OR_DATE",
            friendly_summary="El cliente quiere coordinar una reunión.",
        )

    conversation.refresh_from_db()
    assert conversation.automation_suspended
    notification = NotificationDelivery.objects.get(task=task, recipient=owner)
    assert notification.subject == "Hay una conversación que necesita revisión"
    assert str(task.pk) in notification.secure_url
    assert "cliente" not in notification.subject.casefold()


@pytest.mark.django_db
def test_conversation_resumes_only_after_last_open_task_is_closed(owner) -> None:
    contact, conversation = _contact_conversation(owner)
    first = open_human_task(
        workspace=contact.workspace,
        contact_id=contact.pk,
        conversation_id=conversation.pk,
        kind="REPLY_REVIEW",
        reason="MEETING_OR_DATE",
        friendly_summary="Revisar reunión.",
    )
    second = open_human_task(
        workspace=contact.workspace,
        contact_id=contact.pk,
        conversation_id=conversation.pk,
        kind="REPLY_REVIEW",
        reason="PRICING_OR_QUOTE",
        friendly_summary="Revisar precio.",
    )

    close_human_task(first, actor=owner, dismiss=False, note="Se coordinó por teléfono.")
    conversation.refresh_from_db()
    assert conversation.automation_suspended

    close_human_task(second, actor=owner, dismiss=True, note="Era un mensaje duplicado.")
    conversation.refresh_from_db()
    assert not conversation.automation_suspended
    assert not HumanTask.objects.filter(status=HumanTask.Status.OPEN).exists()


def test_human_task_close_locks_only_the_task_row() -> None:
    query = _human_task_lock_queryset().query
    assert query.select_for_update
    assert query.select_for_update_of == ("self",)


@pytest.mark.django_db
def test_reply_automation_defaults_to_shadow(owner) -> None:
    configuration = ReplyAutomationConfiguration.objects.create(
        workspace=owner.membership.workspace
    )

    assert configuration.mode == ReplyAutomationConfiguration.Mode.SHADOW
    assert configuration.live_enabled_at is None


@pytest.mark.django_db
def test_human_task_database_requires_resolution_actor_and_timestamp(owner) -> None:
    contact, conversation = _contact_conversation(owner)
    task = HumanTask(
        workspace=contact.workspace,
        contact=contact,
        conversation=conversation,
        kind="REPLY_REVIEW",
        reason="OTHER",
        friendly_summary="Revisar.",
        opened_at=timezone.now() - timedelta(minutes=1),
        status=HumanTask.Status.RESOLVED,
    )
    with pytest.raises(ValidationError):
        task.full_clean()


@pytest.mark.django_db
def test_revision_hash_does_not_duplicate_the_body_in_a_decision_manifest(owner) -> None:
    revision = create_knowledge_revision(
        workspace=owner.membership.workspace,
        actor=owner,
        title="Producto",
        category="Catálogo",
        text="Los carbones se ofrecen en distintas medidas.",
    )

    assert isinstance(revision, KnowledgeFactRevision)
    assert len(revision.content_hash) == 64
