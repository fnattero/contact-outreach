from __future__ import annotations

import pytest

from apps.configuration.integrations import redact_provider_error
from apps.integrations.contracts import (
    ReplyClassification,
    ReplyClassificationRequest,
    ValidationProviderError,
)
from apps.integrations.llm import MockLLMProvider
from apps.mailbox.classification import (
    classify_message,
    deterministic_classification,
    newest_reply_text,
)
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


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "Gracias por la info.\nOn Jan 5, 2026 at 9:00 AM, Ventas <a@b.com> wrote:\n> BAJA",
            "Gracias por la info.",
        ),
        (
            "Dale, avisame.\nEl 5 de enero escribió:\n> Desuscribirme ya.",
            "Dale, avisame.",
        ),
        (
            "Ya está.\nEl 5 de enero escribio:\n> BAJA",
            "Ya está.",
        ),
        (
            "Todo bien.\n----- Mensaje original -----\nBAJA",
            "Todo bien.",
        ),
        (
            "Todo bien.\n----- Original Message -----\nBAJA",
            "Todo bien.",
        ),
        (
            "> cita previa\nRespuesta nueva sin encabezado de cita.",
            "Respuesta nueva sin encabezado de cita.",
        ),
    ],
)
def test_newest_reply_text_strips_quoted_history_variants(body: str, expected: str) -> None:
    assert newest_reply_text(body) == expected


class _StubClassificationProvider:
    def __init__(self, result: ReplyClassification | Exception) -> None:
        self._result = result
        self.calls = 0

    def classify_reply(self, request: ReplyClassificationRequest) -> ReplyClassification:
        self.calls += 1
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _unsaved_message(
    *,
    sender: str = "ventas@example.com",
    subject: str = "Consulta",
    body_text: str = "Sí, tenemos interés.",
    headers: dict[str, str] | None = None,
) -> InboundMessage:
    return InboundMessage(
        sender=sender,
        subject=subject,
        body_text=body_text,
        headers=headers or {},
        gmail_message_id="gmail-classify-test",
    )


def test_classify_message_short_circuits_on_deterministic_match_without_calling_provider() -> None:
    message = _unsaved_message(subject="Fuera de la oficina", body_text="Automatic reply")
    provider = _StubClassificationProvider(
        ReplyClassification(classification="OTHER", confidence=0.1)
    )

    classification, confidence, error = classify_message(message=message, provider=provider)

    assert classification == InboundMessage.Classification.AUTO_REPLY
    assert confidence == 1.0
    assert error == ""
    assert provider.calls == 0


def test_classify_message_uses_provider_when_no_deterministic_rule_matches() -> None:
    message = _unsaved_message(body_text="Sí, tenemos interés.")
    provider = MockLLMProvider()

    classification, confidence, error = classify_message(message=message, provider=provider)

    assert classification == InboundMessage.Classification.INTERESTED
    assert 0.0 <= confidence <= 1.0
    assert error == ""


def test_classify_message_never_applies_deterministic_rules_when_disabled() -> None:
    message = _unsaved_message(subject="Fuera de la oficina", body_text="Automatic reply")
    provider = _StubClassificationProvider(
        ReplyClassification(classification="OTHER", confidence=0.4)
    )

    classification, confidence, error = classify_message(
        message=message, provider=provider, apply_deterministic=False
    )

    assert classification == InboundMessage.Classification.OTHER
    assert confidence == 0.4
    assert error == ""
    assert provider.calls == 1


def test_classify_message_redacts_provider_errors_and_returns_other() -> None:
    message = _unsaved_message()
    boom = ValidationProviderError("token de acceso revocado")
    provider = _StubClassificationProvider(boom)

    classification, confidence, error = classify_message(message=message, provider=provider)

    assert classification == InboundMessage.Classification.OTHER
    assert confidence == 0.0
    assert error == redact_provider_error(boom, owner_id=None)
    assert error


def test_classify_message_rejects_unknown_classification_values() -> None:
    message = _unsaved_message()
    provider = _StubClassificationProvider(
        ReplyClassification(classification="MAYBE_LATER", confidence=0.8)
    )

    classification, confidence, error = classify_message(message=message, provider=provider)

    assert classification == InboundMessage.Classification.OTHER
    assert confidence == 0.0
    assert error == "Clasificación IA desconocida."


@pytest.mark.parametrize(
    ("raw_confidence", "expected"),
    [(-0.75, 0.0), (1.6, 1.0)],
)
def test_classify_message_clamps_confidence_into_valid_range(
    raw_confidence: float, expected: float
) -> None:
    message = _unsaved_message()
    provider = _StubClassificationProvider(
        ReplyClassification(classification="INTERESTED", confidence=raw_confidence)
    )

    classification, confidence, error = classify_message(message=message, provider=provider)

    assert classification == InboundMessage.Classification.INTERESTED
    assert confidence == expected
    assert error == ""
