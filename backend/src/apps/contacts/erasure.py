"""Erase what we know about a business while keeping it blocked for good.

Rows are emptied instead of deleted: sent messages, reservations and the same-day ledger still
reference them, and those references are what stop a duplicate send. What remains is the
normalized email address and one-way identity hashes, the minimum needed to recognise the business
if it shows up again and to keep the block. Audit events stay as they are: they record ids and
states, not the business's data.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.accounts.permissions import Capability, require_user_capability
from apps.audit.services import record_event
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import lock_email_eligibility, suppress_email
from apps.contacts.models import (
    CommunicationRestriction,
    Contact,
    EmailAddress,
    Organization,
    OrganizationIdentity,
)

ERASURE_SOURCE = "organization_erasure"
ERASED_TASK_NOTE = "Se eliminaron los datos de este negocio."
ERASED_MESSAGE_REASON = "Se eliminaron los datos de este negocio antes del envío."


@dataclass(frozen=True, slots=True)
class ErasureResult:
    organization: Organization
    already_erased: bool


def _blank(queryset: QuerySet[Any], *field_names: str, **values: Any) -> int:
    """Reset text fields to their empty default in one UPDATE, bypassing per-row save guards."""

    model = queryset.model
    blanks = {name: model._meta.get_field(name).get_default() for name in field_names}
    if any(field.name == "updated_at" for field in model._meta.concrete_fields):
        blanks["updated_at"] = timezone.now()
    return int(queryset.update(**blanks, **values))


def _overture_ids(prospects: QuerySet[Any], identities: QuerySet[OrganizationIdentity]) -> set[str]:
    ids = {
        str(data.get("overture_id", "")).strip()
        for data in prospects.values_list("provider_data", flat=True)
        if isinstance(data, dict)
    }
    ids |= set(
        identities.filter(kind=OrganizationIdentity.Kind.GERS_ID).values_list("value", flat=True)
    )
    return {value for value in ids if value}


def _remove_from_search_runs(workspace_id: Any, overture_ids: set[str]) -> int:
    """Drop the business's row from stored provider pages, which are kept to resume a search."""

    from apps.campaigns.models import SearchRun

    if not overture_ids:
        return 0
    changed = 0
    runs = SearchRun.objects.select_for_update().filter(campaign__workspace_id=workspace_id)
    for run in runs.only("pk", "response_json").iterator():
        payload = run.response_json
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        kept = [
            row
            for row in rows
            if not (isinstance(row, dict) and str(row.get("overture_id", "")) in overture_ids)
        ]
        if len(kept) != len(rows):
            SearchRun.objects.filter(pk=run.pk).update(response_json={**payload, "data": kept})
            changed += 1
    return changed


def drop_erased_rows(provider: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Leave out of a stored provider page every business whose data was erased."""

    from apps.prospects.models import ProspectIdentity

    rows = payload.get("data")
    if not isinstance(rows, list):
        return payload
    hashes: dict[str, str] = {}
    for row in rows:
        if isinstance(row, dict) and str(row.get("overture_id", "")).strip():
            identifier = str(row["overture_id"]).strip()
            hashes[identifier] = hashlib.sha256(f"{provider}:{identifier}".encode()).hexdigest()
    if not hashes:
        return payload
    values = list(hashes.values())
    erased = set(
        ProspectIdentity.objects.filter(
            kind=ProspectIdentity.Kind.PROVIDER_ID,
            value_hash__in=values,
            prospect__erased_at__isnull=False,
        ).values_list("value_hash", flat=True)
    ) | set(
        OrganizationIdentity.objects.filter(
            kind__in=(OrganizationIdentity.Kind.GERS_ID, OrganizationIdentity.Kind.PROVIDER_ID),
            value_hash__in=values,
            organization__erased_at__isnull=False,
        ).values_list("value_hash", flat=True)
    )
    if not erased:
        return payload
    kept = [
        row
        for row in rows
        if not (
            isinstance(row, dict) and hashes.get(str(row.get("overture_id", "")).strip()) in erased
        )
    ]
    return {**payload, "data": kept}


def erase_prospect_data(prospects: QuerySet[Any]) -> int:
    """Empty discovery records: what the catalog, the website and the audience filter gave us."""

    from apps.prospects.models import (
        AIAnalysis,
        Prospect,
        ProspectEmail,
        ProspectRelevanceVerdict,
        WebsiteSnapshot,
    )

    ids = list(prospects.values_list("pk", flat=True))
    if not ids:
        return 0
    _blank(
        Prospect.objects.filter(pk__in=ids),
        "name",
        "normalized_name",
        "address",
        "normalized_address",
        "neighborhood",
        "category",
        "website",
        "business_domain",
        "phone",
        "latitude",
        "longitude",
        "provider_data",
        "last_error",
        pipeline_state=Prospect.PipelineState.SKIPPED_DUPLICATE,
        error_stage="ERASED",
        erased_at=timezone.now(),
    )
    # The normalized address stays, like in the suppression list, so the global de-duplication
    # still recognises the business; everything that described where it came from goes.
    _blank(
        ProspectEmail.objects.filter(prospect_id__in=ids),
        "source_url",
        "source_content_hash",
        "exclusion_reason",
        "invalid_reason",
        original_email=models.F("normalized_email"),
    )
    _blank(
        WebsiteSnapshot.objects.filter(prospect_id__in=ids),
        "requested_url",
        "final_url",
        "excerpt",
        "pages",
        "email_candidates",
        "error",
    )
    _blank(
        AIAnalysis.objects.filter(prospect_id__in=ids),
        "relevance_reason",
        "evidence",
        "subject",
        "body_text",
        "prompt_text",
        "output_json",
        "error",
    )
    _blank(
        ProspectRelevanceVerdict.objects.filter(prospect_id__in=ids),
        "reason",
        "request_manifest",
        "error",
    )
    return len(ids)


def _related_prospects(organization: Organization, normalized_emails: list[str]) -> QuerySet[Any]:
    from apps.prospects.models import Prospect, ProspectIdentity

    own = Prospect.objects.filter(organization=organization)
    hashes = ProspectIdentity.objects.filter(prospect__in=own).values_list("value_hash", flat=True)
    # A discovery that has not been linked to the organization yet is found by its email or by an
    # identity it shares with one that has.
    return (
        Prospect.objects.filter(Q(organization__isnull=True) | Q(organization=organization))
        .filter(
            Q(organization=organization)
            | Q(emails__normalized_email__in=normalized_emails)
            | Q(identities__value_hash__in=list(hashes))
        )
        .distinct()
    )


def _keep_suppressed(normalized_email: str, *, organization: Organization, actor: User) -> None:
    """Keep the address in the suppression list even if a manual block is lifted later."""

    evidence = f"Organization:{organization.pk}"
    entry = suppress_email(
        email=normalized_email,
        reason=SuppressionEntry.Reason.MANUAL,
        actor=actor,
        source=ERASURE_SOURCE,
        evidence=evidence,
    )
    entry = SuppressionEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.reason == SuppressionEntry.Reason.MANUAL and entry.source != ERASURE_SOURCE:
        # A bridge row is removed when its manual restriction is lifted; take it over so the
        # erasure, which can never be lifted, owns it from now on.
        entry.source = ERASURE_SOURCE
        entry.evidence = evidence
    entry.original_email = normalized_email
    entry.save(update_fields=("original_email", "source", "evidence", "updated_at"))


def _refuse_while_sending(organization: Organization, contact: Contact | None) -> None:
    from apps.automation.models import ReplyDecision
    from apps.campaigns.models import OutboundMessage

    in_flight = OutboundMessage.objects.filter(
        Q(organization=organization) | Q(email_address__organization=organization)
    )
    if contact is not None:
        in_flight = in_flight | OutboundMessage.objects.filter(contact=contact)
    sending = in_flight.filter(
        state__in=(OutboundMessage.State.SENDING, OutboundMessage.State.RECONCILING)
    ).exists()
    executing = contact is not None and (
        ReplyDecision.objects.filter(contact=contact, state=ReplyDecision.State.EXECUTING).exists()
    )
    if sending or executing:
        raise ValidationError(
            "Hay un correo a este negocio saliendo en este momento. Probá de nuevo en unos minutos."
        )


def _stop_pending_work(organization: Organization, contact: Contact, actor: User) -> dict[str, int]:
    from apps.automation.models import (
        ContactCommunicationPlan,
        HumanTask,
    )
    from apps.automation.scheduled import _cancel_open_attempts
    from apps.campaigns.models import OutboundMessage
    from apps.contacts.services import _cancel_message_queryset, cancel_pending_campaign_work

    cancelled = cancel_pending_campaign_work(organization, reason=ERASED_MESSAGE_REASON)
    cancelled += _cancel_message_queryset(
        OutboundMessage.objects.filter(
            Q(organization=organization)
            | Q(contact=contact)
            | Q(email_address__organization=organization)
        ),
        reason=ERASED_MESSAGE_REASON,
    )
    plans = list(ContactCommunicationPlan.objects.select_for_update().filter(contact=contact))
    for plan in plans:
        _cancel_open_attempts(plan, reason=ERASED_MESSAGE_REASON)
        plan.state = ContactCommunicationPlan.State.DISABLED
        plan.next_due_at = None
        plan.updated_by = actor
        plan.save(update_fields=("state", "next_due_at", "updated_by", "updated_at"))
    dismissed = HumanTask.objects.filter(contact=contact, status=HumanTask.Status.OPEN).update(
        status=HumanTask.Status.DISMISSED,
        resolved_at=timezone.now(),
        resolved_by=actor,
        resolution_note=ERASED_TASK_NOTE,
        updated_at=timezone.now(),
    )
    return {"cancelled_messages": cancelled, "disabled_plans": len(plans), "tasks": dismissed}


def _erase_mailbox_and_automation(organization: Organization, contact: Contact) -> dict[str, int]:
    from apps.automation.models import (
        ConversationMemory,
        EmailCandidate,
        HumanTask,
        NotificationDelivery,
        ReplyDecision,
        ScheduledContactAttempt,
    )
    from apps.campaigns.models import OutboundMessage
    from apps.contacts.models import Conversation
    from apps.mailbox.models import InboundMessage

    conversations = Conversation.objects.filter(contact=contact)
    inbound = InboundMessage.objects.filter(
        Q(organization=organization) | Q(contact=contact) | Q(conversation__in=conversations)
    )
    inbound_ids = list(inbound.values_list("pk", flat=True))
    counts = {
        "inbound_messages": _blank(
            InboundMessage.objects.filter(pk__in=inbound_ids),
            "sender",
            "recipients",
            "subject",
            "body_text",
            "body_html_sanitized",
            "headers",
            "classification_error",
        ),
        "outbound_messages": _blank(
            OutboundMessage.objects.filter(
                Q(organization=organization)
                | Q(contact=contact)
                | Q(email_address__organization=organization)
                | Q(parent_inbound_id__in=inbound_ids)
            ),
            "subject",
            "body_text",
            "error",
            recipient=models.F("recipient_normalized"),
        ),
    }
    _blank(conversations, "subject")
    _blank(
        EmailCandidate.objects.filter(inbound_id__in=inbound_ids),
        "original_literal",
        "normalized_email",
    )
    decisions = ReplyDecision.objects.filter(Q(contact=contact) | Q(inbound_id__in=inbound_ids))
    _blank(decisions, "proposed_body", "human_reason", "context_manifest", "feedback", "error")
    tasks = HumanTask.objects.filter(Q(contact=contact) | Q(inbound_id__in=inbound_ids))
    _blank(tasks, "reason", friendly_summary=ERASED_TASK_NOTE, resolution_note=ERASED_TASK_NOTE)
    _blank(NotificationDelivery.objects.filter(task__in=tasks), "subject", "error")
    _blank(ConversationMemory.objects.filter(contact=contact), "summary", "source_message_ids")
    _blank(
        ScheduledContactAttempt.objects.filter(plan__contact=contact),
        "context_manifest",
        reason=ERASED_TASK_NOTE,
    )
    return counts


@transaction.atomic
def erase_organization(*, actor: User, organization_id: uuid.UUID | str) -> ErasureResult:
    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    organization = Organization.objects.select_for_update().get(pk=organization_id)
    if organization.workspace_id != membership.workspace_id:
        raise PermissionDenied
    if organization.erased_at is not None:
        return ErasureResult(organization=organization, already_erased=True)

    addresses = list(
        EmailAddress.objects.select_for_update()
        .filter(organization=organization)
        .order_by("normalized_email")
    )
    normalized_emails = [address.normalized_email for address in addresses]
    for normalized in normalized_emails:
        lock_email_eligibility(normalized)
    contact = Contact.objects.select_for_update().filter(organization=organization).first()
    _refuse_while_sending(organization, contact)

    if contact is None:
        contact = Contact.objects.create(
            workspace=organization.workspace,
            organization=organization,
            preferred_email=next((item for item in addresses if item.is_preferred), None),
            created_reason=Contact.CreatedReason.ERASURE,
            created_by=actor,
        )
    CommunicationRestriction.objects.get_or_create(
        workspace=organization.workspace,
        scope=CommunicationRestriction.Scope.CONTACT,
        kind=CommunicationRestriction.Kind.ERASURE,
        contact=contact,
        revoked_at=None,
        defaults={"source": ERASURE_SOURCE, "created_by": actor},
    )
    for normalized in normalized_emails:
        _keep_suppressed(normalized, organization=organization, actor=actor)

    counts = _stop_pending_work(organization, contact, actor)
    counts.update(_erase_mailbox_and_automation(organization, contact))

    from apps.prospects.models import Prospect

    identities = OrganizationIdentity.objects.filter(organization=organization)
    prospect_ids = list(
        _related_prospects(organization, normalized_emails).values_list("pk", flat=True)
    )
    prospects = Prospect.objects.filter(pk__in=prospect_ids)
    overture_ids = _overture_ids(prospects, identities)
    counts["prospects"] = erase_prospect_data(prospects)
    counts["search_pages"] = _remove_from_search_runs(organization.workspace_id, overture_ids)

    _blank(identities, "value", "provenance")
    _blank(
        EmailAddress.objects.filter(organization=organization),
        "label",
        "provenance",
        "source_url",
        "source_content_hash",
        "invalid_reason",
        original_email=models.F("normalized_email"),
    )
    _blank(
        CommunicationRestriction.objects.filter(
            Q(contact=contact) | Q(email_address__organization=organization)
        ),
        "evidence",
    )
    # A lifted manual block must keep a non-empty reason; only its wording goes.
    CommunicationRestriction.objects.filter(
        Q(contact=contact) | Q(email_address__organization=organization),
        revoked_at__isnull=False,
    ).update(revocation_reason=ERASED_TASK_NOTE)

    now = timezone.now()
    Contact.objects.filter(pk=contact.pk).update(
        name="",
        status=Contact.Status.ERASED,
        automation_suspended=True,
        updated_at=now,
    )
    _blank(
        Organization.objects.filter(pk=organization.pk),
        "name",
        "normalized_name",
        "address",
        "normalized_address",
        "website",
        "business_domain",
        "phone",
        "provenance",
        erased_at=now,
    )
    organization.refresh_from_db()
    record_event(
        action="organization.erased",
        entity=organization,
        actor=actor,
        after={**counts, "kept": "normalized_email_and_identity_hashes"},
    )
    return ErasureResult(organization=organization, already_erased=False)
