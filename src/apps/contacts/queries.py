from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import Count, Q, QuerySet

from apps.automation.models import HumanTask, ReplyDecision
from apps.campaigns.models import OutboundMessage
from apps.contacts.models import Contact, Conversation
from apps.mailbox.models import InboundMessage


@dataclass(frozen=True, slots=True)
class TimelineItem:
    direction: str
    happened_at: datetime
    sender: str
    recipient: str
    subject: str
    body: str
    outcome: str
    campaign_name: str
    simulated: bool = False
    needs_attention: bool = False


@dataclass(frozen=True, slots=True)
class ConversationTimeline:
    conversation: Conversation | None
    subject: str
    first_at: datetime
    last_at: datetime
    items: tuple[TimelineItem, ...]
    automation_label: str
    open_task_count: int


def contact_queryset(
    values: Any,
    *,
    workspace_id: UUID | str,
) -> QuerySet[Contact]:
    queryset = (
        Contact.objects.filter(workspace_id=workspace_id)
        .select_related("organization", "preferred_email", "communication_plan")
        .annotate(
            open_task_count=Count(
                "human_tasks",
                filter=Q(human_tasks__status=HumanTask.Status.OPEN),
                distinct=True,
            )
        )
    )
    query = str(values.get("q", "")).strip()
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(organization__name__icontains=query)
            | Q(organization__email_addresses__normalized_email__icontains=query)
        ).distinct()
    status = str(values.get("status", ""))
    if status in Contact.Status.values:
        queryset = queryset.filter(status=status)
    attention = str(values.get("attention", ""))
    if attention == "open":
        queryset = queryset.filter(open_task_count__gt=0)
    elif attention == "clear":
        queryset = queryset.filter(open_task_count=0)
    return queryset.order_by("-last_interaction_at", "-created_at")


def attention_queryset(*, workspace_id: UUID | str) -> QuerySet[HumanTask]:
    return (
        HumanTask.objects.filter(workspace_id=workspace_id, status=HumanTask.Status.OPEN)
        .select_related(
            "contact__organization",
            "contact__preferred_email",
            "conversation",
            "inbound",
        )
        .order_by("-opened_at")
    )


def _conversation_automation_label(
    conversation: Conversation | None,
    decisions: list[ReplyDecision],
    open_tasks: int,
) -> str:
    if open_tasks:
        return "Necesita que lo revises"
    if conversation is not None and conversation.automation_suspended:
        return "Respuesta automática pausada"
    if not decisions:
        return "Sin acciones automáticas"
    decision = max(decisions, key=lambda item: item.created_at)
    if decision.state == ReplyDecision.State.COMPLETED:
        return "Respondido automáticamente"
    if decision.state == ReplyDecision.State.SHADOW_RECORDED:
        return "Modo de observación: se preparó una sugerencia y no se envió"
    if decision.state == ReplyDecision.State.NO_ACTION:
        return "No hacía falta responder"
    if decision.state in {ReplyDecision.State.HUMAN_REQUIRED, ReplyDecision.State.FAILED}:
        return "Necesita que lo revises"
    return "Análisis de respuesta en curso"


def conversation_timelines(
    contact: Contact,
    *,
    include_simulations: bool,
) -> tuple[ConversationTimeline, ...]:
    conversations = list(
        Conversation.objects.filter(contact=contact)
        .select_related("connection")
        .order_by("created_at")
    )
    inbound_messages = list(
        InboundMessage.objects.filter(Q(contact=contact) | Q(organization=contact.organization))
        .select_related("related_outbound__campaign", "conversation")
        .order_by("external_at", "created_at")
    )
    outbound_states = [OutboundMessage.State.SENT]
    if include_simulations:
        outbound_states.append(OutboundMessage.State.DRY_RUN_COMPLETED)
    outbound_messages = list(
        OutboundMessage.objects.filter(
            Q(contact=contact) | Q(organization=contact.organization),
            state__in=outbound_states,
        )
        .select_related("campaign", "conversation")
        .order_by("sent_at", "simulated_at", "created_at")
    )
    decisions = list(ReplyDecision.objects.filter(contact=contact).select_related("conversation"))
    tasks = list(HumanTask.objects.filter(contact=contact, status=HumanTask.Status.OPEN))

    groups: dict[str, dict[str, Any]] = {}
    for conversation in conversations:
        groups[f"conversation:{conversation.pk}"] = {
            "conversation": conversation,
            "thread_id": conversation.gmail_thread_id,
            "subject": conversation.subject,
            "items": [],
        }

    def group_for(conversation_id: object | None, thread_id: str, subject: str) -> dict[str, Any]:
        key = f"conversation:{conversation_id}" if conversation_id is not None else ""
        if key and key in groups:
            return groups[key]
        matching_key = next(
            (
                candidate_key
                for candidate_key, candidate in groups.items()
                if thread_id and candidate["thread_id"] == thread_id
            ),
            "",
        )
        if matching_key:
            return groups[matching_key]
        fallback_key = f"thread:{thread_id}" if thread_id else "without-thread"
        return groups.setdefault(
            fallback_key,
            {
                "conversation": None,
                "thread_id": thread_id,
                "subject": subject or "Conversación sin asunto",
                "items": [],
            },
        )

    task_inbound_ids = {task.inbound_id for task in tasks if task.inbound_id is not None}
    for outbound in outbound_messages:
        happened_at = outbound.sent_at or outbound.simulated_at or outbound.created_at
        group = group_for(outbound.conversation_id, outbound.gmail_thread_id, outbound.subject)
        group["items"].append(
            TimelineItem(
                direction="outbound",
                happened_at=happened_at,
                sender="Tu equipo",
                recipient=outbound.recipient,
                subject=outbound.subject,
                body=outbound.body_text,
                outcome=(
                    "Modo de prueba: no se envió"
                    if outbound.state == OutboundMessage.State.DRY_RUN_COMPLETED
                    else outbound.get_kind_display()
                ),
                campaign_name=outbound.campaign.name if outbound.campaign is not None else "",
                simulated=outbound.state == OutboundMessage.State.DRY_RUN_COMPLETED,
            )
        )
    for inbound in inbound_messages:
        group = group_for(inbound.conversation_id, inbound.gmail_thread_id, inbound.subject)
        group["items"].append(
            TimelineItem(
                direction="inbound",
                happened_at=inbound.external_at,
                sender=inbound.sender,
                recipient="Tu equipo",
                subject=inbound.subject,
                body=inbound.body_text,
                outcome=(
                    "Respuesta automática recibida"
                    if inbound.classification == InboundMessage.Classification.AUTO_REPLY
                    else inbound.get_classification_display()
                ),
                campaign_name=(
                    inbound.related_outbound.campaign.name
                    if inbound.related_outbound.campaign is not None
                    else ""
                ),
                needs_attention=inbound.pk in task_inbound_ids,
            )
        )

    timelines: list[ConversationTimeline] = []
    for group in groups.values():
        items = sorted(group["items"], key=lambda item: item.happened_at)
        if not items:
            continue
        conversation = group["conversation"]
        group_decisions = [
            item
            for item in decisions
            if conversation is not None and item.conversation_id == conversation.pk
        ]
        group_task_count = sum(
            1
            for task in tasks
            if conversation is not None and task.conversation_id == conversation.pk
        )
        timelines.append(
            ConversationTimeline(
                conversation=conversation,
                subject=group["subject"] or items[0].subject or "Conversación sin asunto",
                first_at=items[0].happened_at,
                last_at=items[-1].happened_at,
                items=tuple(items),
                automation_label=_conversation_automation_label(
                    conversation,
                    group_decisions,
                    group_task_count,
                ),
                open_task_count=group_task_count,
            )
        )
    return tuple(sorted(timelines, key=lambda item: item.first_at))
