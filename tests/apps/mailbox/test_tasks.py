from __future__ import annotations

from unittest.mock import Mock

import pytest
from django.contrib.auth.models import User

from apps.audit.models import BackgroundJob
from apps.integrations.contracts import AuthenticationError, RetryableProviderError
from apps.mailbox import tasks
from apps.mailbox.models import GmailConnection


def test_delivery_task_wrappers_and_recovery_schedulers(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(tasks, "deliver_message", lambda message_id: f"sent:{message_id}")
    monkeypatch.setattr(tasks, "reconcile_message", lambda message_id: f"found:{message_id}")
    assert tasks.deliver_message_task("one") == "sent:one"
    assert tasks.reconcile_message_task("one") == "found:one"

    deliver_delay = Mock()
    reconcile_delay = Mock()
    monkeypatch.setattr(tasks, "pending_message_ids", lambda: ("one", "two"))
    monkeypatch.setattr(tasks, "recoverable_message_ids", lambda: ("three",))
    monkeypatch.setattr(tasks.deliver_message_task, "delay", deliver_delay)
    monkeypatch.setattr(tasks.reconcile_message_task, "delay", reconcile_delay)
    completed = Mock(return_value=1)
    monkeypatch.setattr(tasks, "complete_drained_campaigns", completed)

    assert tasks.deliver_outbound_messages() == 2
    assert deliver_delay.call_count == 2
    completed.assert_called_once_with()
    assert tasks.recover_ambiguous_sends() == 1
    reconcile_delay.assert_called_once_with("three")


@pytest.mark.django_db
def test_sync_scheduler_dispatches_connected_account(
    monkeypatch: pytest.MonkeyPatch,
    owner: User,
) -> None:
    connection = GmailConnection.objects.create(
        owner=owner,
        status=GmailConnection.Status.CONNECTED,
    )
    monkeypatch.setattr(tasks, "sync_gmail_connection", lambda connection_id: 3)
    assert tasks.sync_gmail_connection_task(str(connection.pk)) == 3

    delay = Mock()
    monkeypatch.setattr(tasks.sync_gmail_connection_task, "delay", delay)
    assert tasks.sync_gmail_replies() == 1
    delay.assert_called_once_with(str(connection.pk))


@pytest.mark.django_db
def test_sync_job_marks_non_retryable_error_failed(
    monkeypatch: pytest.MonkeyPatch,
    owner: User,
) -> None:
    connection = GmailConnection.objects.create(
        owner=owner,
        status=GmailConnection.Status.CONNECTED,
    )

    def authentication_failure(connection_id: str) -> int:
        del connection_id
        raise AuthenticationError("OAuth revocado")

    monkeypatch.setattr(tasks, "sync_gmail_connection", authentication_failure)
    with pytest.raises(AuthenticationError, match="OAuth revocado"):
        tasks.sync_gmail_connection_task(str(connection.pk))

    job = BackgroundJob.objects.get(idempotency_key=f"gmail-sync:{connection.pk}")
    assert job.state == BackgroundJob.State.FAILED
    assert job.finished_at is not None


@pytest.mark.django_db
def test_sync_job_marks_exhausted_retryable_error_failed(
    monkeypatch: pytest.MonkeyPatch,
    owner: User,
) -> None:
    connection = GmailConnection.objects.create(
        owner=owner,
        status=GmailConnection.Status.CONNECTED,
    )

    def transient_failure(connection_id: str) -> int:
        del connection_id
        raise RetryableProviderError("Proveedor temporalmente fuera de servicio")

    monkeypatch.setattr(tasks, "sync_gmail_connection", transient_failure)
    tasks.sync_gmail_connection_task.push_request(retries=3)
    try:
        with pytest.raises(RetryableProviderError, match="temporalmente"):
            tasks.sync_gmail_connection_task.run(str(connection.pk))
    finally:
        tasks.sync_gmail_connection_task.pop_request()

    job = BackgroundJob.objects.get(idempotency_key=f"gmail-sync:{connection.pk}")
    assert job.state == BackgroundJob.State.FAILED
    assert job.finished_at is not None


def test_manual_reply_tasks_only_dispatch_authorized_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tasks, "deliver_manual_reply", lambda message_id: f"sent:{message_id}")
    monkeypatch.setattr(
        tasks,
        "reconcile_manual_reply",
        lambda message_id: f"reconciled:{message_id}",
    )
    assert tasks.deliver_manual_reply_task("manual-one") == "sent:manual-one"
    assert tasks.reconcile_manual_reply_task("manual-two") == "reconciled:manual-two"

    deliver_delay = Mock()
    reconcile_delay = Mock()
    monkeypatch.setattr(tasks, "pending_manual_reply_ids", lambda: ("authorized",))
    monkeypatch.setattr(tasks, "recoverable_manual_reply_ids", lambda: ("ambiguous",))
    monkeypatch.setattr(tasks.deliver_manual_reply_task, "delay", deliver_delay)
    monkeypatch.setattr(tasks.reconcile_manual_reply_task, "delay", reconcile_delay)
    assert tasks.dispatch_manual_replies() == 2
    deliver_delay.assert_called_once_with("authorized")
    reconcile_delay.assert_called_once_with("ambiguous")
