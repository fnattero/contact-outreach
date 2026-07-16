from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path

from django.core.exceptions import ValidationError


@dataclass(frozen=True, slots=True)
class BuiltMessage:
    raw: bytes
    sha256: str


def deterministic_message_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"<{digest}@contact-outreach.local>"


def build_message(
    *,
    sender: str,
    recipient: str,
    subject: str,
    body_text: str,
    message_id: str,
    sent_at: datetime,
    campaign_header: str,
    message_header: str,
    pdf_bytes: bytes | None = None,
    pdf_filename: str = "catalogo.pdf",
) -> BuiltMessage:
    if not sender or not recipient or "\n" in sender or "\n" in recipient:
        raise ValidationError("El remitente o destinatario MIME no es válido.")
    message = EmailMessage(policy=SMTP)
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = sent_at
    message["Message-ID"] = message_id
    message["X-Contact-Outreach-Campaign"] = campaign_header
    message["X-Contact-Outreach-Message"] = message_header
    message.set_content(body_text, subtype="plain", charset="utf-8")
    if pdf_bytes is not None:
        safe_name = Path(pdf_filename).name
        if not safe_name.casefold().endswith(".pdf"):
            safe_name = "catalogo.pdf"
        message.add_attachment(
            pdf_bytes,
            maintype="application",
            subtype="pdf",
            filename=safe_name,
        )
    raw = message.as_bytes(policy=SMTP)
    return BuiltMessage(raw=raw, sha256=hashlib.sha256(raw).hexdigest())


def build_reply_message(
    *,
    sender: str,
    recipient: str,
    subject: str,
    body_text: str,
    message_id: str,
    sent_at: datetime,
    thread_references: tuple[str, ...],
    in_reply_to: str,
    campaign_header: str,
    message_header: str,
) -> BuiltMessage:
    if not sender or not recipient or "\n" in sender or "\n" in recipient:
        raise ValidationError("El remitente o destinatario MIME no es válido.")
    if not in_reply_to:
        raise ValidationError("La respuesta requiere In-Reply-To.")
    message = EmailMessage(policy=SMTP)
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = sent_at
    message["Message-ID"] = message_id
    message["In-Reply-To"] = in_reply_to
    message["References"] = " ".join(thread_references)
    message["X-Contact-Outreach-Campaign"] = campaign_header
    message["X-Contact-Outreach-Message"] = message_header
    message.set_content(body_text, subtype="plain", charset="utf-8")
    raw = message.as_bytes(policy=SMTP)
    return BuiltMessage(raw=raw, sha256=hashlib.sha256(raw).hexdigest())
