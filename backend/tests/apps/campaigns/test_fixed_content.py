from __future__ import annotations

import pytest

from apps.campaigns.content import (
    INITIAL_BODY,
    INITIAL_SUBJECT,
    REDIRECT_ACK_BODY,
    REFERRED_PROPOSAL_BODY,
    REFERRED_PROPOSAL_SUBJECT,
    REMINDER_BODY,
    freeze_message_content,
)


def test_default_campaign_messages_are_exact_and_have_no_placeholders() -> None:
    assert INITIAL_SUBJECT == "Propuesta comercial"
    assert INITIAL_BODY == (
        "Buen día:\n\n"
        "Nos ponemos en contacto para acercarle nuestra propuesta de componentes industriales y "
        "compartir nuestros catálogos. Trabajamos con distintas medidas y aplicaciones para "
        "equipos y herramientas eléctricas. Si le resulta de interés, puede responder este "
        "correo y con gusto ampliaremos la información.\n\nSaludos."
    )
    assert REMINDER_BODY == (
        "Buen día:\n\n"
        "Retomamos nuestro correo anterior para saber si pudo revisar la propuesta y los "
        "catálogos. Si necesita información sobre alguna medida o aplicación, puede responder "
        "este mensaje.\n\nSaludos."
    )
    assert REFERRED_PROPOSAL_SUBJECT == "Propuesta comercial"
    assert REFERRED_PROPOSAL_BODY == (
        "Buen día:\n\n"
        "Nos indicaron que esta es la dirección adecuada para enviar nuestra propuesta comercial. "
        "Adjuntamos la información y los catálogos correspondientes. Quedamos a disposición ante "
        "cualquier consulta.\n\nSaludos."
    )
    assert REDIRECT_ACK_BODY == (
        "Perfecto, muchas gracias. La propuesta fue enviada a la dirección indicada."
    )
    assert "{{" not in INITIAL_BODY + REMINDER_BODY + REFERRED_PROPOSAL_BODY


def test_signature_is_appended_deterministically_and_hashed() -> None:
    first = freeze_message_content(
        subject=INITIAL_SUBJECT,
        body=INITIAL_BODY,
        signature="Empresa Demo\nventas@example.com",
    )
    second = freeze_message_content(
        subject=INITIAL_SUBJECT,
        body=INITIAL_BODY,
        signature="Empresa Demo\nventas@example.com",
    )

    assert first.rendered_body == f"{INITIAL_BODY}\n\nEmpresa Demo\nventas@example.com"
    assert first.content_hash == second.content_hash


def test_campaign_content_rejects_template_placeholders() -> None:
    with pytest.raises(ValueError, match="datos variables"):
        freeze_message_content(subject="Hola {{ nombre }}", body=INITIAL_BODY, signature="")
