from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parseaddr
from statistics import median

from django.core.exceptions import ValidationError
from django.db.models import QuerySet

from apps.accounts.models import Workspace
from apps.automation.models import HumanTask, ReplyDecision
from apps.campaigns.models import Campaign, OutboundMessage
from apps.compliance.services import normalize_email
from apps.contacts.models import Contact, EmailAddress
from apps.mailbox.models import InboundMessage

from .queries import outbound_workspace_filter


@dataclass(frozen=True, slots=True)
class SummaryMetrics:
    unique_initial_recipients: int
    initial_messages_sent: int
    reminders_sent: int
    automatic_replies_sent: int
    scheduled_contacts_sent: int
    unique_human_responders: int
    response_rate: float | None
    positive_response_rate: float | None
    contacts_created: int
    bounce_rate: float | None
    unsubscribe_rate: float | None
    automatically_resolved: int
    human_required: int
    open_human_tasks: int
    median_first_response: timedelta | None
    median_human_intervention: timedelta | None
    responses_after_initial: int
    responses_after_reminder: int


@dataclass(slots=True)
class _InitialUnit:
    key: str
    campaign_id: uuid.UUID | None
    enrollment_id: uuid.UUID | None
    recipient: str
    sent_at: datetime | None
    first_response_at: datetime | None = None
    positive: bool = False
    bounced: bool = False
    unsubscribed: bool = False
    reminder_times: tuple[datetime, ...] = ()


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _median_duration(seconds: list[float]) -> timedelta | None:
    return timedelta(seconds=median(seconds)) if seconds else None


def _campaign_scope(
    workspace: Workspace,
    campaign_id: uuid.UUID | str | None,
) -> QuerySet[Campaign]:
    scope = Campaign.objects.filter(workspace=workspace)
    if campaign_id is not None:
        scope = scope.filter(pk=campaign_id)
    return scope


def _normalized_sender(value: str) -> str:
    literal = parseaddr(value)[1]
    if not literal:
        return ""
    try:
        return normalize_email(literal)
    except ValidationError:
        return ""


def workspace_summary_metrics(
    workspace: Workspace,
    *,
    campaign_id: uuid.UUID | str | None = None,
) -> SummaryMetrics:
    """Derive all dashboard values from durable effects, never mutable counters."""

    campaigns = _campaign_scope(workspace, campaign_id)
    if campaign_id is None:
        # Conversational and scheduled messages do not necessarily belong to a
        # campaign.  Resolve their Workspace through every durable attribution
        # path so the all-time summary includes them without adding counters.
        outbound = OutboundMessage.objects.filter(
            outbound_workspace_filter(workspace.pk),
            state=OutboundMessage.State.SENT,
        ).distinct()
    else:
        outbound = OutboundMessage.objects.filter(
            campaign__in=campaigns,
            state=OutboundMessage.State.SENT,
        )
    initial = outbound.filter(
        kind__in=(OutboundMessage.Kind.FIRST_CONTACT, OutboundMessage.Kind.INITIAL)
    )
    units: dict[str, _InitialUnit] = {}
    enrollment_units: dict[uuid.UUID, str] = {}
    legacy_routes: dict[tuple[uuid.UUID | None, str], list[str]] = {}
    initial_rows = initial.order_by("sent_at", "created_at").values(
        "pk",
        "campaign_id",
        "campaign_enrollment_id",
        "recipient_normalized",
        "sent_at",
    )
    for initial_row in initial_rows:
        enrollment_id = initial_row["campaign_enrollment_id"]
        key = f"enrollment:{enrollment_id}" if enrollment_id else f"legacy:{initial_row['pk']}"
        if key in units:
            continue
        recipient = str(initial_row["recipient_normalized"] or "").strip().casefold()
        unit = _InitialUnit(
            key=key,
            campaign_id=initial_row["campaign_id"],
            enrollment_id=enrollment_id,
            recipient=recipient,
            sent_at=initial_row["sent_at"],
        )
        units[key] = unit
        if enrollment_id is not None:
            enrollment_units[enrollment_id] = key
        else:
            legacy_routes.setdefault((unit.campaign_id, recipient), []).append(key)
    denominator = len(units)

    def unit_for_event(
        *,
        enrollment_id: uuid.UUID | None,
        campaign_id: uuid.UUID | None,
        recipient: str,
        occurred_at: datetime,
    ) -> _InitialUnit | None:
        if enrollment_id is not None:
            key = enrollment_units.get(enrollment_id)
            if key is not None:
                return units[key]
        route = legacy_routes.get((campaign_id, recipient.strip().casefold()), [])
        eligible: list[_InitialUnit] = []
        for key in route:
            candidate = units[key]
            if candidate.sent_at is None or candidate.sent_at <= occurred_at:
                eligible.append(candidate)
        return eligible[-1] if eligible else (units[route[-1]] if route else None)

    inbound = InboundMessage.objects.filter(connection__workspace=workspace)
    if campaign_id is not None:
        inbound = inbound.filter(related_outbound__campaign_id=campaign_id)
    reminder_map: dict[str, list[datetime]] = {}
    for reminder_row in outbound.filter(kind=OutboundMessage.Kind.CAMPAIGN_REMINDER).values(
        "campaign_id",
        "campaign_enrollment_id",
        "recipient_normalized",
        "sent_at",
    ):
        sent_at = reminder_row["sent_at"]
        if sent_at is None:
            continue
        matched_unit = unit_for_event(
            enrollment_id=reminder_row["campaign_enrollment_id"],
            campaign_id=reminder_row["campaign_id"],
            recipient=str(reminder_row["recipient_normalized"] or ""),
            occurred_at=sent_at,
        )
        if matched_unit is not None:
            reminder_map.setdefault(matched_unit.key, []).append(sent_at)
    for key, values in reminder_map.items():
        units[key].reminder_times = tuple(sorted(values))

    responder_contact_ids: set[uuid.UUID] = set()
    unlinked_responder_emails: set[str] = set()
    unlinked_responder_fallbacks: set[str] = set()
    inbound_rows = inbound.values(
        "campaign_enrollment_id",
        "contact_id",
        "sender",
        "external_at",
        "classification",
        "is_human",
        "related_outbound__campaign_id",
        "related_outbound__recipient_normalized",
    )
    for inbound_row in inbound_rows:
        occurred_at = inbound_row["external_at"]
        matched_unit = unit_for_event(
            enrollment_id=inbound_row["campaign_enrollment_id"],
            campaign_id=inbound_row["related_outbound__campaign_id"],
            recipient=str(inbound_row["related_outbound__recipient_normalized"] or ""),
            occurred_at=occurred_at,
        )
        classification = inbound_row["classification"]
        if matched_unit is not None:
            if classification == InboundMessage.Classification.BOUNCE:
                matched_unit.bounced = True
            if classification == InboundMessage.Classification.UNSUBSCRIBE:
                matched_unit.unsubscribed = True
            if inbound_row["is_human"]:
                if (
                    matched_unit.first_response_at is None
                    or occurred_at < matched_unit.first_response_at
                ):
                    matched_unit.first_response_at = occurred_at
                if classification == InboundMessage.Classification.INTERESTED:
                    matched_unit.positive = True
        if not inbound_row["is_human"]:
            continue
        contact_id = inbound_row["contact_id"]
        if contact_id is not None:
            responder_contact_ids.add(contact_id)
            continue
        sender = str(inbound_row["sender"] or "")
        normalized_sender = _normalized_sender(sender)
        if normalized_sender:
            unlinked_responder_emails.add(normalized_sender)
        elif sender.strip():
            unlinked_responder_fallbacks.add(sender.strip().casefold())

    linked_responder_emails = set(
        EmailAddress.objects.filter(
            organization__contact__pk__in=responder_contact_ids
        ).values_list("normalized_email", flat=True)
    )
    unique_human_responders = (
        len(responder_contact_ids)
        + len(unlinked_responder_emails - linked_responder_emails)
        + len(unlinked_responder_fallbacks)
    )

    scoped_inbound_ids = inbound.values_list("pk", flat=True)
    contacts = Contact.objects.filter(workspace=workspace)
    if campaign_id is not None:
        contacts = contacts.filter(source_inbound_message_id__in=scoped_inbound_ids)
    decisions = ReplyDecision.objects.filter(workspace=workspace)
    tasks = HumanTask.objects.filter(workspace=workspace)
    if campaign_id is not None:
        decisions = decisions.filter(inbound__related_outbound__campaign_id=campaign_id)
        tasks = tasks.filter(inbound__related_outbound__campaign_id=campaign_id)

    first_response_seconds: list[float] = []
    responses_after_initial = 0
    responses_after_reminder = 0
    for unit in units.values():
        first_reply_at = unit.first_response_at
        if first_reply_at is None:
            continue
        if unit.sent_at is not None:
            delta = (first_reply_at - unit.sent_at).total_seconds()
            if delta >= 0:
                first_response_seconds.append(delta)
        if any(sent_at <= first_reply_at for sent_at in unit.reminder_times):
            responses_after_reminder += 1
        else:
            responses_after_initial += 1

    intervention_seconds = [
        (resolved_at - opened_at).total_seconds()
        for opened_at, resolved_at in tasks.exclude(resolved_at=None).values_list(
            "opened_at", "resolved_at"
        )
        if resolved_at is not None and resolved_at >= opened_at
    ]
    human_decision_ids = set(decisions.filter(action="HUMAN").values_list("pk", flat=True))
    human_required_count = (
        len(human_decision_ids) + tasks.exclude(decision_id__in=human_decision_ids).count()
    )
    return SummaryMetrics(
        unique_initial_recipients=len(
            {unit.recipient for unit in units.values() if unit.recipient}
        ),
        initial_messages_sent=initial.count(),
        reminders_sent=outbound.filter(kind=OutboundMessage.Kind.CAMPAIGN_REMINDER).count(),
        automatic_replies_sent=outbound.filter(kind=OutboundMessage.Kind.AUTOMATIC_REPLY).count(),
        scheduled_contacts_sent=outbound.filter(
            kind=OutboundMessage.Kind.SCHEDULED_CONTACT
        ).count(),
        unique_human_responders=unique_human_responders,
        response_rate=_rate(
            sum(unit.first_response_at is not None for unit in units.values()), denominator
        ),
        positive_response_rate=_rate(sum(unit.positive for unit in units.values()), denominator),
        contacts_created=contacts.count(),
        bounce_rate=_rate(sum(unit.bounced for unit in units.values()), denominator),
        unsubscribe_rate=_rate(sum(unit.unsubscribed for unit in units.values()), denominator),
        automatically_resolved=decisions.filter(state=ReplyDecision.State.COMPLETED).count(),
        human_required=human_required_count,
        open_human_tasks=tasks.filter(status=HumanTask.Status.OPEN).count(),
        median_first_response=_median_duration(first_response_seconds),
        median_human_intervention=_median_duration(intervention_seconds),
        responses_after_initial=responses_after_initial,
        responses_after_reminder=responses_after_reminder,
    )


def duration_label(value: timedelta | None) -> str:
    if value is None:
        return "—"
    total_minutes = max(0, round(value.total_seconds() / 60))
    if total_minutes < 60:
        return f"{total_minutes} min"
    hours, minutes = divmod(total_minutes, 60)
    if hours < 48:
        return f"{hours} h" if not minutes else f"{hours} h {minutes} min"
    days, remaining_hours = divmod(hours, 24)
    return f"{days} días" if not remaining_hours else f"{days} días {remaining_hours} h"
