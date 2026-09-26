from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.campaigns.content import INITIAL_BODY, INITIAL_SUBJECT
from apps.configuration.message_templates import (
    create_message_template_revision,
    ensure_default_message_templates,
)
from apps.configuration.models import WorkspaceMessageTemplateRevision


@pytest.mark.django_db
def test_ensure_default_message_templates_creates_and_is_idempotent(owner: User) -> None:
    workspace = owner.membership.workspace

    active = ensure_default_message_templates(workspace)

    assert active.initial.kind == WorkspaceMessageTemplateRevision.Kind.INITIAL
    assert active.initial.subject == INITIAL_SUBJECT
    assert active.initial.body == INITIAL_BODY
    assert active.initial.revision == 1
    assert active.initial.active
    assert active.reminder.kind == WorkspaceMessageTemplateRevision.Kind.REMINDER
    assert active.reminder.subject == ""
    assert active.referred_proposal.kind == WorkspaceMessageTemplateRevision.Kind.REFERRED_PROPOSAL

    again = ensure_default_message_templates(workspace)

    assert again.initial.pk == active.initial.pk
    assert again.reminder.pk == active.reminder.pk
    assert again.referred_proposal.pk == active.referred_proposal.pk
    assert WorkspaceMessageTemplateRevision.objects.filter(workspace=workspace).count() == 3


@pytest.mark.django_db
def test_ensure_default_message_templates_recreates_missing_kind(owner: User) -> None:
    workspace = owner.membership.workspace
    ensure_default_message_templates(workspace)
    WorkspaceMessageTemplateRevision.objects.filter(
        workspace=workspace, kind=WorkspaceMessageTemplateRevision.Kind.INITIAL
    ).delete()

    active = ensure_default_message_templates(workspace)

    assert active.initial.revision == 1
    assert active.initial.subject == INITIAL_SUBJECT
    assert active.initial.body == INITIAL_BODY
    assert active.initial.active


@pytest.mark.django_db
def test_create_message_template_revision_starts_at_one_when_none_active(owner: User) -> None:
    workspace = owner.membership.workspace
    ensure_default_message_templates(workspace)
    WorkspaceMessageTemplateRevision.objects.filter(
        workspace=workspace, kind=WorkspaceMessageTemplateRevision.Kind.REMINDER
    ).delete()

    revision = create_message_template_revision(
        workspace=workspace,
        actor=owner,
        kind=WorkspaceMessageTemplateRevision.Kind.REMINDER,
        subject="",
        body="Primer recordatorio de esta variante.",
    )

    assert revision.revision == 1
    assert revision.active


@pytest.mark.django_db
def test_create_message_template_revision_supersedes_previous_and_records_audit(
    owner: User,
) -> None:
    workspace = owner.membership.workspace
    ensure_default_message_templates(workspace)
    previous = WorkspaceMessageTemplateRevision.objects.get(
        workspace=workspace,
        kind=WorkspaceMessageTemplateRevision.Kind.INITIAL,
        active=True,
    )

    revision = create_message_template_revision(
        workspace=workspace,
        actor=owner,
        kind=WorkspaceMessageTemplateRevision.Kind.INITIAL,
        subject="Nueva propuesta",
        body="Nuevo cuerpo del mensaje inicial.",
    )

    previous.refresh_from_db()
    assert previous.active is False
    assert revision.active is True
    assert revision.revision == 2
    assert revision.subject == "Nueva propuesta"
    assert revision.body == "Nuevo cuerpo del mensaje inicial."
    assert revision.approved_by_id == owner.pk
    assert revision.content_hash != previous.content_hash

    event = AuditEvent.objects.get(action="message_template.approved")
    assert event.after["kind"] == WorkspaceMessageTemplateRevision.Kind.INITIAL
    assert event.after["revision"] == 2
    assert event.actor_id == owner.pk


@pytest.mark.django_db
def test_create_message_template_revision_strips_whitespace(owner: User) -> None:
    workspace = owner.membership.workspace
    previous_revision = (
        WorkspaceMessageTemplateRevision.objects.filter(
            workspace=workspace,
            kind=WorkspaceMessageTemplateRevision.Kind.REMINDER,
            active=True,
        )
        .values_list("revision", flat=True)
        .first()
        or 0
    )

    revision = create_message_template_revision(
        workspace=workspace,
        actor=owner,
        kind=WorkspaceMessageTemplateRevision.Kind.REMINDER,
        subject="  ",
        body="  Recordatorio con espacios.  ",
    )

    assert revision.revision == previous_revision + 1
    assert revision.subject == ""
    assert revision.body == "Recordatorio con espacios."


@pytest.mark.django_db
def test_create_message_template_revision_rejects_empty_body(owner: User) -> None:
    workspace = owner.membership.workspace

    with pytest.raises(ValidationError):
        create_message_template_revision(
            workspace=workspace,
            actor=owner,
            kind=WorkspaceMessageTemplateRevision.Kind.INITIAL,
            subject="Asunto",
            body="   ",
        )


@pytest.mark.django_db
def test_create_message_template_revision_rejects_empty_subject_for_non_reminder(
    owner: User,
) -> None:
    workspace = owner.membership.workspace

    with pytest.raises(ValidationError):
        create_message_template_revision(
            workspace=workspace,
            actor=owner,
            kind=WorkspaceMessageTemplateRevision.Kind.REFERRED_PROPOSAL,
            subject="   ",
            body="Cuerpo válido.",
        )


@pytest.mark.django_db
def test_create_message_template_revision_allows_empty_subject_for_reminder(
    owner: User,
) -> None:
    workspace = owner.membership.workspace

    revision = create_message_template_revision(
        workspace=workspace,
        actor=owner,
        kind=WorkspaceMessageTemplateRevision.Kind.REMINDER,
        subject="",
        body="Recordatorio sin asunto propio.",
    )

    assert revision.subject == ""


@pytest.mark.django_db
def test_create_message_template_revision_rejects_placeholder_markers(owner: User) -> None:
    workspace = owner.membership.workspace

    with pytest.raises(ValidationError):
        create_message_template_revision(
            workspace=workspace,
            actor=owner,
            kind=WorkspaceMessageTemplateRevision.Kind.INITIAL,
            subject="Asunto",
            body="Hola {{ nombre }}, tenemos una propuesta.",
        )


@pytest.mark.django_db
def test_create_message_template_revision_rejects_invalid_kind(owner: User) -> None:
    workspace = owner.membership.workspace

    with pytest.raises(ValidationError):
        create_message_template_revision(
            workspace=workspace,
            actor=owner,
            kind="NOT_A_KIND",
            subject="Asunto",
            body="Cuerpo válido.",
        )


@pytest.mark.django_db
def test_create_message_template_revision_requires_manage_configuration_capability(
    owner: User,
) -> None:
    workspace = owner.membership.workspace
    vendedor = User.objects.create_user(username="vendedor", password="correct-password")
    assert vendedor.membership.role == "VENDEDOR"

    with pytest.raises(PermissionDenied):
        create_message_template_revision(
            workspace=workspace,
            actor=vendedor,
            kind=WorkspaceMessageTemplateRevision.Kind.INITIAL,
            subject="Asunto",
            body="Cuerpo válido.",
        )


@pytest.mark.django_db
def test_message_templates_view_requires_authentication(client: Client) -> None:
    assert client.get(reverse("message-templates")).status_code == 302


@pytest.mark.django_db
def test_message_templates_view_denies_without_capability(client: Client, owner: User) -> None:
    vendedor = User.objects.create_user(username="vendedor", password="correct-password")
    del owner
    client.force_login(vendedor)

    response = client.get(reverse("message-templates"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_message_templates_view_renders_current_defaults(client: Client, owner: User) -> None:
    client.force_login(owner)

    response = client.get(reverse("message-templates"))

    assert response.status_code == 200
    content = response.content.decode()
    assert INITIAL_SUBJECT in content
    assert "Recordatorio" in content
    assert (
        WorkspaceMessageTemplateRevision.objects.filter(
            workspace=owner.membership.workspace
        ).count()
        == 3
    )


@pytest.mark.django_db
def test_message_templates_view_submits_new_revision(client: Client, owner: User) -> None:
    client.force_login(owner)
    client.get(reverse("message-templates"))

    response = client.post(
        reverse("message-templates"),
        {
            "kind": WorkspaceMessageTemplateRevision.Kind.INITIAL,
            "subject": "Propuesta renovada",
            "body": "Cuerpo actualizado del mensaje inicial.",
        },
    )

    assert response.status_code == 302
    updated = WorkspaceMessageTemplateRevision.objects.get(
        workspace=owner.membership.workspace,
        kind=WorkspaceMessageTemplateRevision.Kind.INITIAL,
        active=True,
    )
    assert updated.revision == 2
    assert updated.subject == "Propuesta renovada"

    page = client.get(reverse("message-templates"))
    assert "Propuesta renovada" in page.content.decode()


@pytest.mark.django_db
def test_message_templates_view_shows_service_validation_error(client: Client, owner: User) -> None:
    client.force_login(owner)

    response = client.post(
        reverse("message-templates"),
        {
            "kind": WorkspaceMessageTemplateRevision.Kind.REFERRED_PROPOSAL,
            "subject": "",
            "body": "Cuerpo sin asunto para un mensaje que lo requiere.",
        },
    )

    assert response.status_code == 200
    assert "El asunto no puede quedar vacío." in response.content.decode()


@pytest.mark.django_db
def test_message_templates_view_rejects_missing_body_at_form_level(
    client: Client, owner: User
) -> None:
    client.force_login(owner)

    response = client.post(
        reverse("message-templates"),
        {
            "kind": WorkspaceMessageTemplateRevision.Kind.INITIAL,
            "subject": "Asunto",
            "body": "",
        },
    )

    assert response.status_code == 200
    form = response.context["form"]
    assert not form.is_valid()
    assert "body" in form.errors
