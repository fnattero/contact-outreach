from __future__ import annotations

from celery import shared_task

from apps.campaigns.delivery import (
    complete_drained_campaigns,
    deliver_message,
    pending_message_ids,
    reconcile_message,
    recoverable_message_ids,
)


@shared_task(name="mailbox.deliver_message")  # type: ignore[untyped-decorator]
def deliver_message_task(message_id: str) -> str:
    return deliver_message(message_id)


@shared_task(name="mailbox.deliver_outbound_messages")  # type: ignore[untyped-decorator]
def deliver_outbound_messages() -> int:
    message_ids = pending_message_ids()
    for message_id in message_ids:
        deliver_message_task.delay(str(message_id))
    complete_drained_campaigns()
    return len(message_ids)


@shared_task(name="mailbox.reconcile_message")  # type: ignore[untyped-decorator]
def reconcile_message_task(message_id: str) -> str:
    return reconcile_message(message_id)


@shared_task(name="mailbox.recover_ambiguous_sends")  # type: ignore[untyped-decorator]
def recover_ambiguous_sends() -> int:
    message_ids = recoverable_message_ids()
    for message_id in message_ids:
        reconcile_message_task.delay(str(message_id))
    return len(message_ids)
