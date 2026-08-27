from hashlib import sha256

from django.db import migrations
from django.utils import timezone

INITIAL_SUBJECT = "Propuesta comercial"
INITIAL_BODY = (
    "Buen día:\n\n"
    "Nos ponemos en contacto para acercarle nuestra propuesta de carbones para motores y "
    "compartir nuestros catálogos. Trabajamos con distintas medidas y aplicaciones para "
    "motores y herramientas eléctricas. Si le resulta de interés, puede responder este "
    "correo y con gusto ampliaremos la información.\n\nSaludos."
)
REMINDER_BODY = (
    "Buen día:\n\n"
    "Retomamos nuestro correo anterior para saber si pudo revisar la propuesta y los "
    "catálogos. Si necesita información sobre alguna medida o aplicación, puede responder "
    "este mensaje.\n\nSaludos."
)
REFERRED_BODY = (
    "Buen día:\n\n"
    "Nos indicaron que esta es la dirección adecuada para enviar nuestra propuesta comercial. "
    "Adjuntamos la información y los catálogos correspondientes. Quedamos a disposición ante "
    "cualquier consulta.\n\nSaludos."
)


def seed_templates(apps, schema_editor):
    Workspace = apps.get_model("accounts", "Workspace")
    Template = apps.get_model("configuration", "WorkspaceMessageTemplateRevision")
    now = timezone.now()
    defaults = (
        ("INITIAL", INITIAL_SUBJECT, INITIAL_BODY),
        ("REMINDER", "", REMINDER_BODY),
        ("REFERRED_PROPOSAL", INITIAL_SUBJECT, REFERRED_BODY),
    )
    for workspace_id in Workspace.objects.values_list("pk", flat=True):
        for kind, subject, body in defaults:
            canonical = "\n".join((subject, body))
            Template.objects.get_or_create(
                workspace_id=workspace_id,
                kind=kind,
                revision=1,
                defaults={
                    "subject": subject,
                    "body": body,
                    "content_hash": sha256(canonical.encode()).hexdigest(),
                    "approved_at": now,
                    "active": True,
                },
            )


class Migration(migrations.Migration):
    dependencies = [("configuration", "0010_workspacemessagetemplaterevision")]

    operations = [migrations.RunPython(seed_templates, migrations.RunPython.noop)]
