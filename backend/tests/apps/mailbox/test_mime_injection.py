"""Hostile values must never become extra headers or extra recipients (SECURITY.md sections 7, 8)."""

from __future__ import annotations

from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.mailbox.mime import build_message, build_reply_message

BASE: dict[str, Any] = {
    "sender": "ventas@empresa.example",
    "recipient": "cliente@example.com",
    "subject": "Propuesta",
    "body_text": "Hola.",
    "message_id": "<id@contact-outreach.local>",
    "sent_at": datetime(2026, 10, 1, 12, tzinfo=UTC),
    "campaign_header": "campaign-1",
    "message_header": "message-1",
}
REPLY = {**BASE, "thread_references": ("<a@x>",), "in_reply_to": "<a@x>"}

LINE_BREAKS = ["\r\n", "\n", "\r"]


def _headers(raw: bytes):
    return BytesParser(policy=policy.default).parsebytes(raw)


@pytest.mark.parametrize("field", ["subject", "message_id", "campaign_header", "message_header"])
@pytest.mark.parametrize("breaker", LINE_BREAKS)
@pytest.mark.parametrize("builder", [build_message, build_reply_message])
def test_a_line_break_in_any_header_value_cannot_inject_another_header(
    builder, field: str, breaker: str
) -> None:
    kwargs = {**(REPLY if builder is build_reply_message else BASE)}
    kwargs[field] = f"valor{breaker}Bcc: atacante@evil.example"

    with pytest.raises((ValidationError, ValueError)):
        builder(**kwargs)


@pytest.mark.parametrize("breaker", LINE_BREAKS)
def test_a_line_break_in_the_pdf_filename_cannot_inject_a_header(breaker: str) -> None:
    with pytest.raises((ValidationError, ValueError)):
        build_message(**BASE, pdf_bytes=b"%PDF-1.4", pdf_filename=f"a{breaker}Bcc: e@evil.example.pdf")


@pytest.mark.parametrize(
    "recipient",
    [
        "a@x.com, b@y.com",
        "a@x.com; b@y.com",
        "a@x.com b@y.com",
        "Ana <a@x.com>",
        "<a@x.com>",
        '"a@x.com"@y.com',
        "a@x.com\r\nBcc: e@evil.example",
        "a@x.com\nBcc: e@evil.example",
        "a@x.com\rBcc: e@evil.example",
        "a@x.com\n",
        " a@x.com",
        "a@x.com\t",
        "a@x.com\x00",
        "a@b@c.com",
        "sin-arroba.example",
        "",
    ],
)
@pytest.mark.parametrize("builder", [build_message, build_reply_message])
def test_only_one_plain_address_can_be_the_recipient(builder, recipient: str) -> None:
    kwargs = {**(REPLY if builder is build_reply_message else BASE), "recipient": recipient}

    with pytest.raises(ValidationError, match="no es válido"):
        builder(**kwargs)


@pytest.mark.parametrize("builder", [build_message, build_reply_message])
def test_a_plain_address_is_sent_to_exactly_that_address(builder) -> None:
    kwargs = {**(REPLY if builder is build_reply_message else BASE)}

    message = _headers(builder(**kwargs).raw)

    assert message["To"] == "cliente@example.com"
    assert message["Bcc"] is None and message["Cc"] is None
    assert message["From"] == "ventas@empresa.example"


@pytest.mark.parametrize("sender", ["", "a@x.com\nBcc: e@evil.example", "a@x.com\rBcc: e@evil.example"])
def test_the_sender_cannot_carry_line_breaks(sender: str) -> None:
    with pytest.raises(ValidationError, match="no es válido"):
        build_message(**{**BASE, "sender": sender})


def test_the_reply_needs_the_message_it_answers() -> None:
    with pytest.raises(ValidationError, match="identificador del mensaje anterior"):
        build_reply_message(**{**REPLY, "in_reply_to": ""})


def test_header_looking_text_in_the_body_stays_in_the_body() -> None:
    body = "Hola.\r\nBcc: atacante@evil.example\r\n\r\nTo: otro@evil.example\n"

    built = build_message(**{**BASE, "body_text": body})

    message = _headers(built.raw)
    assert message["Bcc"] is None
    assert message.get_all("To") == ["cliente@example.com"]
    assert "Bcc: atacante@evil.example" in message.get_content()


def test_a_traversal_filename_is_reduced_to_its_base_name() -> None:
    built = build_message(**BASE, pdf_bytes=b"%PDF-1.4\n", pdf_filename="../../etc/passwd.pdf")

    names = [part.get_filename() for part in _headers(built.raw).iter_attachments()]

    assert names == ["passwd.pdf"]


@pytest.mark.parametrize("filename", ["", "no-extension", "evil.exe", "evil.pdf.exe", "../../etc/shadow"])
def test_a_non_pdf_filename_is_replaced_by_a_safe_default(filename: str) -> None:
    built = build_message(**BASE, pdf_bytes=b"%PDF-1.4\n", pdf_filename=filename)

    names = [part.get_filename() for part in _headers(built.raw).iter_attachments()]

    assert names == ["catalogo.pdf"]


def test_the_message_id_is_deterministic_so_a_retry_cannot_create_a_second_email() -> None:
    from apps.mailbox.mime import deterministic_message_id

    assert deterministic_message_id("key-1") == deterministic_message_id("key-1")
    assert deterministic_message_id("key-1") != deterministic_message_id("key-2")
    assert deterministic_message_id("key-1").endswith("@contact-outreach.local>")
