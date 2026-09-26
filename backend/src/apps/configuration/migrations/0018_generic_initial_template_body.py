from hashlib import sha256

from django.db import migrations

SUBJECT = "Propuesta comercial"

# Exactly what migration 0011 seeded. Only a revision still holding this text is rewritten,
# so a workspace that edited its own initial message keeps it untouched.
SEEDED_BODY = (
    "Buen día:\n\n"
    "Nos ponemos en contacto para acercarle nuestra propuesta de carbones para motores y "
    "compartir nuestros catálogos. Trabajamos con distintas medidas y aplicaciones para "
    "motores y herramientas eléctricas. Si le resulta de interés, puede responder este "
    "correo y con gusto ampliaremos la información.\n\nSaludos."
)
GENERIC_BODY = (
    "Buen día:\n\n"
    "Nos ponemos en contacto para acercarle nuestra propuesta de componentes industriales y "
    "compartir nuestros catálogos. Trabajamos con distintas medidas y aplicaciones para "
    "equipos y herramientas eléctricas. Si le resulta de interés, puede responder este "
    "correo y con gusto ampliaremos la información.\n\nSaludos."
)


def _content_hash(subject: str, body: str) -> str:
    return sha256("\n".join((subject, body)).encode()).hexdigest()


def _rewrite(apps, *, old: str, new: str) -> None:
    Template = apps.get_model("configuration", "WorkspaceMessageTemplateRevision")
    Template.objects.filter(kind="INITIAL", revision=1, body=old).update(
        body=new,
        content_hash=_content_hash(SUBJECT, new),
    )


def apply_generic_body(apps, schema_editor):
    _rewrite(apps, old=SEEDED_BODY, new=GENERIC_BODY)


def restore_seeded_body(apps, schema_editor):
    _rewrite(apps, old=GENERIC_BODY, new=SEEDED_BODY)


class Migration(migrations.Migration):
    dependencies = [("configuration", "0017_style_only_automatic_reply_prompt")]

    operations = [migrations.RunPython(apply_generic_body, restore_seeded_body)]
