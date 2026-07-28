from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

INITIAL_SUBJECT = "Propuesta comercial"
INITIAL_BODY = (
    "Buen día:\n\n"
    "Nos ponemos en contacto para acercarle nuestra propuesta de carbones para motores y "
    "compartir nuestros catálogos. Trabajamos con distintas medidas y aplicaciones para "
    "motores y herramientas eléctricas. Si le resulta de interés, puede responder este "
    "correo y con gusto ampliaremos la información.\n\n"
    "Saludos."
)

REMINDER_BODY = (
    "Buen día:\n\n"
    "Retomamos nuestro correo anterior para saber si pudo revisar la propuesta y los "
    "catálogos. Si necesita información sobre alguna medida o aplicación, puede responder "
    "este mensaje.\n\n"
    "Saludos."
)

REFERRED_PROPOSAL_SUBJECT = "Propuesta comercial"
REFERRED_PROPOSAL_BODY = (
    "Buen día:\n\n"
    "Nos indicaron que esta es la dirección adecuada para enviar nuestra propuesta comercial. "
    "Adjuntamos la información y los catálogos correspondientes. Quedamos a disposición ante "
    "cualquier consulta.\n\n"
    "Saludos."
)

REDIRECT_ACK_BODY = "Perfecto, muchas gracias. La propuesta fue enviada a la dirección indicada."


@dataclass(frozen=True, slots=True)
class FrozenMessageContent:
    subject: str
    body: str
    signature: str
    rendered_body: str
    content_hash: str


def validate_fixed_content(*, subject: str, body: str) -> None:
    if not subject.strip() or not body.strip():
        raise ValueError("El asunto y el mensaje no pueden quedar vacíos.")
    placeholder_markers = ("{{", "}}", "{%", "%}")
    if any(marker in subject or marker in body for marker in placeholder_markers):
        raise ValueError("Los mensajes de campaña no admiten datos variables por destinatario.")


def freeze_message_content(
    *,
    subject: str,
    body: str,
    signature: str,
) -> FrozenMessageContent:
    clean_subject = subject.strip()
    clean_body = body.strip()
    clean_signature = signature.strip()
    validate_fixed_content(subject=clean_subject, body=clean_body)
    rendered = f"{clean_body}\n\n{clean_signature}" if clean_signature else clean_body
    canonical = "\n".join((clean_subject, clean_body, clean_signature))
    return FrozenMessageContent(
        subject=clean_subject,
        body=clean_body,
        signature=clean_signature,
        rendered_body=rendered,
        content_hash=sha256(canonical.encode()).hexdigest(),
    )
