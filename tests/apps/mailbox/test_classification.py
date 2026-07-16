from __future__ import annotations

import pytest

from apps.integrations.contracts import ReplyClassificationRequest
from apps.integrations.llm import MockLLMProvider
from apps.mailbox.classification import deterministic_classification
from apps.mailbox.models import InboundMessage


@pytest.mark.parametrize(
    ("sender", "subject", "body", "headers", "expected"),
    [
        (
            "mailer-daemon@example.com",
            "Delivery Status Notification",
            "User unknown\n> respondé BAJA",
            {},
            InboundMessage.Classification.BOUNCE,
        ),
        (
            "ventas@example.com",
            "Consulta",
            "Por favor, darme de BAJA.",
            {},
            InboundMessage.Classification.UNSUBSCRIBE,
        ),
        (
            "ventas@example.com",
            "Respuesta automática",
            "Estoy fuera de la oficina.",
            {"Auto-Submitted": "auto-replied"},
            InboundMessage.Classification.AUTO_REPLY,
        ),
    ],
)
def test_deterministic_reply_rules_run_before_ai(
    sender: str,
    subject: str,
    body: str,
    headers: dict[str, str],
    expected: str,
) -> None:
    result = deterministic_classification(
        sender=sender,
        subject=subject,
        body_text=body,
        headers=headers,
    )
    assert result is not None
    assert result[0] == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Sí, tenemos interés.", InboundMessage.Classification.INTERESTED),
        ("No me interesa, gracias.", InboundMessage.Classification.NOT_INTERESTED),
        ("Recibido.", InboundMessage.Classification.OTHER),
    ],
)
def test_fake_ai_covers_remaining_reply_classes(body: str, expected: str) -> None:
    result = MockLLMProvider().classify_reply(
        ReplyClassificationRequest(
            body_text=body,
            correlation_id="test",
            idempotency_key="classification-test",
        )
    )
    assert result.classification == expected
