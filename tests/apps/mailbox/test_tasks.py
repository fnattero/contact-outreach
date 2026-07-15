from __future__ import annotations

from unittest.mock import Mock

from apps.mailbox import tasks


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
