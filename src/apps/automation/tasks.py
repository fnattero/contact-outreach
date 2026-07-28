from __future__ import annotations

from celery import shared_task

from apps.automation.execution import (
    AUTOMATIC_KINDS,
    deliver_authorized_outbound,
    execute_reply_decision,
    pending_reply_decision_ids,
    reconcile_authorized_outbound,
    recoverable_outbound_ids,
)
from apps.automation.notifications import (
    deliver_notification,
    pending_notification_ids,
    reconcile_notification,
    recoverable_notification_ids,
)
from apps.automation.retrieval import refresh_knowledge_revision_embedding
from apps.automation.scheduled import (
    complete_scheduled_contact_attempt,
    create_due_scheduled_attempts,
    process_scheduled_contact_attempt,
    scheduled_outbound_ids_for_completion,
)
from apps.campaigns.models import OutboundMessage


@shared_task(name="automation.execute_reply_decision")  # type: ignore[untyped-decorator]
def execute_reply_decision_task(decision_id: str) -> str:
    return execute_reply_decision(decision_id)


@shared_task(name="automation.deliver_authorized_outbound")  # type: ignore[untyped-decorator]
def deliver_authorized_outbound_task(message_id: str) -> str:
    return deliver_authorized_outbound(message_id)


@shared_task(name="automation.reconcile_authorized_outbound")  # type: ignore[untyped-decorator]
def reconcile_authorized_outbound_task(message_id: str) -> str:
    return reconcile_authorized_outbound(message_id)


@shared_task(name="automation.deliver_notification")  # type: ignore[untyped-decorator]
def deliver_notification_task(delivery_id: str) -> str:
    return deliver_notification(delivery_id)


@shared_task(name="automation.reconcile_notification")  # type: ignore[untyped-decorator]
def reconcile_notification_task(delivery_id: str) -> str:
    return reconcile_notification(delivery_id)


@shared_task(name="automation.process_scheduled_contact_attempt")  # type: ignore[untyped-decorator]
def process_scheduled_contact_attempt_task(attempt_id: str) -> str:
    return process_scheduled_contact_attempt(attempt_id).state


@shared_task(name="automation.refresh_knowledge_revision_embedding")  # type: ignore[untyped-decorator]
def refresh_knowledge_revision_embedding_task(revision_id: str) -> str:
    return refresh_knowledge_revision_embedding(revision_id)


@shared_task(name="automation.dispatch_scheduled_contacts")  # type: ignore[untyped-decorator]
def dispatch_scheduled_contacts() -> int:
    attempt_ids = create_due_scheduled_attempts()
    for attempt_id in attempt_ids:
        process_scheduled_contact_attempt_task.delay(str(attempt_id))
    return len(attempt_ids)


@shared_task(name="automation.recover_actions")  # type: ignore[untyped-decorator]
def recover_automation_actions() -> int:
    queued = 0
    for decision_id in pending_reply_decision_ids():
        execute_reply_decision_task.delay(str(decision_id))
        queued += 1

    message_rows = OutboundMessage.objects.filter(pk__in=recoverable_outbound_ids()).values_list(
        "pk", "kind", "state"
    )
    for message_id, kind, state in message_rows:
        if state in {
            OutboundMessage.State.SENDING,
            OutboundMessage.State.RECONCILING,
        }:
            reconcile_authorized_outbound_task.delay(str(message_id))
            queued += 1
        elif kind not in AUTOMATIC_KINDS:
            deliver_authorized_outbound_task.delay(str(message_id))
            queued += 1

    for delivery_id in pending_notification_ids():
        deliver_notification_task.delay(str(delivery_id))
        queued += 1
    for delivery_id in recoverable_notification_ids():
        reconcile_notification_task.delay(str(delivery_id))
        queued += 1

    for message_id in scheduled_outbound_ids_for_completion():
        complete_scheduled_contact_attempt(message_id)
        queued += 1
    return queued
