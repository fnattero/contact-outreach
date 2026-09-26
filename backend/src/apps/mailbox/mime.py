from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path

from django.core.exceptions import ValidationError

MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_SOURCE_PDF_BYTES = 17 * 1024 * 1024
MAX_SERIALIZED_MIME_BYTES = 24 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PdfAttachment:
    filename: str
    content: bytes


@dataclass(frozen=True, slots=True)
class BuiltMessage:
    raw: bytes
    sha256: str
    size: int


def deterministic_message_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"<{digest}@contact-outreach.local>"


def _safe_pdf_filename(filename: str) -> str:
    safe_name = Path(filename).name
    if not safe_name.casefold().endswith(".pdf"):
        return "catalogo.pdf"
    return safe_name


def _validated_pdf_attachments(
    *,
    pdf_attachments: Sequence[PdfAttachment],
    pdf_bytes: bytes | None,
    pdf_filename: str,
) -> tuple[PdfAttachment, ...]:
    if pdf_bytes is not None and pdf_attachments:
        raise ValidationError("Indicá una sola colección de PDFs para el correo.")
    attachments = (
        tuple(pdf_attachments)
        if pdf_bytes is None
        else (PdfAttachment(filename=pdf_filename, content=pdf_bytes),)
    )
    total = 0
    for attachment in attachments:
        size = len(attachment.content)
        if size <= 0:
            raise ValidationError("Uno de los PDFs está vacío.")
        if size > MAX_PDF_BYTES:
            raise ValidationError("Uno de los PDFs supera el máximo de 15 MiB.")
        if not attachment.content.startswith(b"%PDF-"):
            raise ValidationError("Uno de los adjuntos no es un PDF válido.")
        total += size
    if total > MAX_SOURCE_PDF_BYTES:
        raise ValidationError("Los PDFs superan el límite combinado de 17 MiB.")
    return attachments


def _finish_message(message: EmailMessage) -> BuiltMessage:
    raw = message.as_bytes(policy=SMTP)
    size = len(raw)
    if size > MAX_SERIALIZED_MIME_BYTES:
        raise ValidationError("El correo completo supera el máximo de 24 MiB.")
    return BuiltMessage(raw=raw, sha256=hashlib.sha256(raw).hexdigest(), size=size)


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
    pdf_attachments: Sequence[PdfAttachment] = (),
    pdf_bytes: bytes | None = None,
    pdf_filename: str = "catalogo.pdf",
) -> BuiltMessage:
    if not sender or not recipient or "\n" in sender or "\n" in recipient:
        raise ValidationError("El remitente o destinatario del correo no es válido.")
    message = EmailMessage(policy=SMTP)
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = sent_at
    message["Message-ID"] = message_id
    message["X-Contact-Outreach-Campaign"] = campaign_header
    message["X-Contact-Outreach-Message"] = message_header
    message.set_content(body_text, subtype="plain", charset="utf-8")
    attachments = _validated_pdf_attachments(
        pdf_attachments=pdf_attachments,
        pdf_bytes=pdf_bytes,
        pdf_filename=pdf_filename,
    )
    for attachment in attachments:
        message.add_attachment(
            attachment.content,
            maintype="application",
            subtype="pdf",
            filename=_safe_pdf_filename(attachment.filename),
        )
    return _finish_message(message)


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
        raise ValidationError("El remitente o destinatario del correo no es válido.")
    if not in_reply_to:
        raise ValidationError("La respuesta necesita el identificador del mensaje anterior.")
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
    return _finish_message(message)
