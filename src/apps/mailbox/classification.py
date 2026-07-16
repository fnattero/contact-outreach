from __future__ import annotations

import re

from apps.integrations.contracts import (
    LLMProvider,
    ProviderError,
    ReplyClassificationRequest,
)
from apps.mailbox.models import InboundMessage

_BOUNCE_SUBJECT = re.compile(
    r"delivery status notification|undeliver(?:ed|able)|mail delivery|mensaje no entregado",
    re.IGNORECASE,
)
_BOUNCE_BODY = re.compile(
    r"recipient address rejected|user unknown|mailbox (?:does not exist|unavailable|full)|"
    r"permanent failure|no se pudo entregar",
    re.IGNORECASE,
)
_UNSUBSCRIBE = re.compile(
    r"\b(?:baja|desuscrib(?:irme|ite)|darme de baja|no (?:me )?(?:contacten|escriban)|"
    r"dejar de recibir)\b",
    re.IGNORECASE,
)
_AUTO_REPLY = re.compile(
    r"respuesta autom[aá]tica|fuera de (?:la )?oficina|out of office|automatic reply|vacation",
    re.IGNORECASE,
)
_VALID_CLASSIFICATIONS = frozenset(InboundMessage.Classification.values)


def newest_reply_text(body_text: str) -> str:
    lines: list[str] = []
    for line in body_text.splitlines():
        stripped = line.strip()
        lowered = stripped.casefold()
        if stripped.startswith(">"):
            continue
        if lowered.startswith("on ") and lowered.endswith(" wrote:"):
            break
        if lowered.startswith("el ") and ("escribió:" in lowered or "escribio:" in lowered):
            break
        if "mensaje original" in lowered or "original message" in lowered:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def deterministic_classification(
    *,
    sender: str,
    subject: str,
    body_text: str,
    headers: dict[str, str],
) -> tuple[str, float] | None:
    sender_lower = sender.casefold()
    newest_text = newest_reply_text(body_text)
    if (
        "mailer-daemon" in sender_lower
        or "postmaster@" in sender_lower
        or _BOUNCE_SUBJECT.search(subject)
        or _BOUNCE_BODY.search(newest_text)
    ):
        return InboundMessage.Classification.BOUNCE, 1.0
    if _UNSUBSCRIBE.search(newest_text):
        return InboundMessage.Classification.UNSUBSCRIBE, 1.0
    auto_submitted = headers.get("Auto-Submitted", "").casefold()
    precedence = headers.get("Precedence", "").casefold()
    if (
        (auto_submitted and auto_submitted != "no")
        or headers.get("X-Autoreply", "")
        or headers.get("X-Auto-Response-Suppress", "")
        or precedence in {"auto_reply", "bulk", "junk"}
        or _AUTO_REPLY.search(subject)
        or _AUTO_REPLY.search(newest_text)
    ):
        return InboundMessage.Classification.AUTO_REPLY, 1.0
    return None


def classify_message(
    *,
    message: InboundMessage,
    provider: LLMProvider,
    apply_deterministic: bool = True,
) -> tuple[str, float, str]:
    if apply_deterministic:
        deterministic = deterministic_classification(
            sender=message.sender,
            subject=message.subject,
            body_text=message.body_text,
            headers=message.headers,
        )
        if deterministic is not None:
            return deterministic[0], deterministic[1], ""
    try:
        result = provider.classify_reply(
            ReplyClassificationRequest(
                body_text=newest_reply_text(message.body_text),
                correlation_id=str(message.pk),
                idempotency_key=f"classify-reply:{message.gmail_message_id}",
            )
        )
    except ProviderError as exc:
        return InboundMessage.Classification.OTHER, 0.0, str(exc)[:500]
    if result.classification not in _VALID_CLASSIFICATIONS:
        return InboundMessage.Classification.OTHER, 0.0, "Clasificación IA desconocida."
    confidence = max(0.0, min(1.0, result.confidence))
    return result.classification, confidence, ""
