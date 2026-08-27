from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Workspace
from apps.accounts.permissions import Capability, require_user_capability
from apps.audit.services import record_event
from apps.campaigns.content import (
    INITIAL_BODY,
    INITIAL_SUBJECT,
    REFERRED_PROPOSAL_BODY,
    REFERRED_PROPOSAL_SUBJECT,
    REMINDER_BODY,
)
from apps.configuration.models import WorkspaceMessageTemplateRevision


@dataclass(frozen=True, slots=True)
class ActiveMessageTemplates:
    initial: WorkspaceMessageTemplateRevision
    reminder: WorkspaceMessageTemplateRevision
    referred_proposal: WorkspaceMessageTemplateRevision


DEFAULTS = {
    WorkspaceMessageTemplateRevision.Kind.INITIAL: (INITIAL_SUBJECT, INITIAL_BODY),
    WorkspaceMessageTemplateRevision.Kind.REMINDER: ("", REMINDER_BODY),
    WorkspaceMessageTemplateRevision.Kind.REFERRED_PROPOSAL: (
        REFERRED_PROPOSAL_SUBJECT,
        REFERRED_PROPOSAL_BODY,
    ),
}


def _content_hash(subject: str, body: str) -> str:
    return sha256("\n".join((subject, body)).encode()).hexdigest()


def _validate_content(*, kind: str, subject: str, body: str) -> tuple[str, str]:
    clean_subject = subject.strip()
    clean_body = body.strip()
    if not clean_body:
        raise ValidationError("El mensaje no puede quedar vacío.")
    if kind != WorkspaceMessageTemplateRevision.Kind.REMINDER and not clean_subject:
        raise ValidationError("El asunto no puede quedar vacío.")
    if any(marker in clean_subject or marker in clean_body for marker in ("{{", "}}", "{%", "%}")):
        raise ValidationError("Los mensajes de campaña no admiten datos variables.")
    return clean_subject, clean_body


@transaction.atomic
def ensure_default_message_templates(workspace: Workspace) -> ActiveMessageTemplates:
    active: dict[str, WorkspaceMessageTemplateRevision] = {}
    for kind, (subject, body) in DEFAULTS.items():
        template = WorkspaceMessageTemplateRevision.objects.filter(
            workspace=workspace,
            kind=kind,
            active=True,
        ).first()
        if template is None:
            template = WorkspaceMessageTemplateRevision.objects.create(
                workspace=workspace,
                kind=kind,
                subject=subject,
                body=body,
                revision=1,
                content_hash=_content_hash(subject, body),
                approved_at=timezone.now(),
                active=True,
            )
        active[kind] = template
    return ActiveMessageTemplates(
        initial=active[WorkspaceMessageTemplateRevision.Kind.INITIAL],
        reminder=active[WorkspaceMessageTemplateRevision.Kind.REMINDER],
        referred_proposal=active[WorkspaceMessageTemplateRevision.Kind.REFERRED_PROPOSAL],
    )


@transaction.atomic
def create_message_template_revision(
    *,
    workspace: Workspace,
    actor: User,
    kind: str,
    subject: str,
    body: str,
) -> WorkspaceMessageTemplateRevision:
    require_user_capability(actor, Capability.MANAGE_CONFIGURATION, workspace_id=workspace.pk)
    if kind not in WorkspaceMessageTemplateRevision.Kind.values:
        raise ValidationError("El tipo de mensaje no es válido.")
    clean_subject, clean_body = _validate_content(kind=kind, subject=subject, body=body)
    current = (
        WorkspaceMessageTemplateRevision.objects.select_for_update()
        .filter(workspace=workspace, kind=kind, active=True)
        .first()
    )
    revision_number = (current.revision if current else 0) + 1
    if current is not None:
        current.active = False
        current.save(update_fields=("active", "updated_at"))
    revision = WorkspaceMessageTemplateRevision.objects.create(
        workspace=workspace,
        kind=kind,
        subject=clean_subject,
        body=clean_body,
        revision=revision_number,
        content_hash=_content_hash(clean_subject, clean_body),
        approved_at=timezone.now(),
        approved_by=actor,
        active=True,
    )
    record_event(
        action="message_template.approved",
        entity=revision,
        actor=actor,
        after={"kind": kind, "revision": revision_number},
    )
    return revision
