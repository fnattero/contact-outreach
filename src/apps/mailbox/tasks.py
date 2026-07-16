from __future__ import annotations

from celery import shared_task

from apps.campaigns.delivery import (
    complete_drained_campaigns,
    deliver_message,
    pending_message_ids,
    reconcile_message,
    recoverable_message_ids,
)
from apps.integrations.contracts import RetryableProviderError
from apps.mailbox.manual import (
    deliver_manual_reply,
    pending_manual_reply_ids,
    reconcile_manual_reply,
    recoverable_manual_reply_ids,
)
from apps.mailbox.models import GmailConnection
from apps.mailbox.sync import sync_gmail_connection


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


@shared_task(  # type: ignore[untyped-decorator]
    name="mailbox.sync_gmail_connection",
    autoretry_for=(RetryableProviderError,),
    retry_backoff=True,
    max_retries=3,
)
def sync_gmail_connection_task(connection_id: str) -> int:
    return sync_gmail_connection(connection_id)


@shared_task(name="mailbox.sync_gmail_replies")  # type: ignore[untyped-decorator]
def sync_gmail_replies() -> int:
    connection_ids = GmailConnection.objects.filter(
        status=GmailConnection.Status.CONNECTED
    ).values_list("pk", flat=True)
    count = 0
    for connection_id in connection_ids:
        sync_gmail_connection_task.delay(str(connection_id))
        count += 1
    return count


@shared_task(name="mailbox.deliver_manual_reply")  # type: ignore[untyped-decorator]
def deliver_manual_reply_task(message_id: str) -> str:
    return deliver_manual_reply(message_id)


@shared_task(name="mailbox.reconcile_manual_reply")  # type: ignore[untyped-decorator]
def reconcile_manual_reply_task(message_id: str) -> str:
    return reconcile_manual_reply(message_id)


@shared_task(name="mailbox.dispatch_manual_replies")  # type: ignore[untyped-decorator]
def dispatch_manual_replies() -> int:
    pending = pending_manual_reply_ids()
    recoverable = recoverable_manual_reply_ids()
    for message_id in pending:
        deliver_manual_reply_task.delay(str(message_id))
    for message_id in recoverable:
        reconcile_manual_reply_task.delay(str(message_id))
    return len(pending) + len(recoverable)
