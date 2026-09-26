from django.db import migrations, models


STYLE_ONLY_AUTOMATIC_REPLY_PROMPT = "\n\n".join(
    (
        "Tono: cordial, claro y profesional, como un mail real escrito por una persona "
        "ocupada pero atenta. Evitá exagerar el entusiasmo, las frases de marketing vacías "
        "y la presión comercial.",
        "Extensión y estructura: contestá la pregunta concreta en la primera frase y usá "
        "uno a tres párrafos cortos. Integrá la información relevante con tus propias "
        "palabras y evitá listas salvo que realmente aclaren la respuesta.",
        "Idioma: respondé en el idioma del contacto y conservá un registro natural para "
        "ese idioma. No hables sobre el sistema, la automatización ni procesos internos.",
        "Iniciativa: mantené una actitud consultiva y poco invasiva. No agregues llamados a "
        "la acción comerciales que no sean relevantes para la consulta. Cuando ayude a "
        "avanzar, cerrá con una próxima acción simple y concreta.",
    )
)

LEGACY_DEFAULT_PREFIX = (
    "Tu rol: sos una persona del equipo comercial y técnico de la empresa. Respondés "
    "mails de clientes o contactos existentes que escriben a la casilla de contacto."
)
LEGACY_DEFAULT_MARKER = "Regla especial para coordinación humana:"
LEGACY_DEFAULT_SUFFIX = (
    "Cuando ayude, cerrá con una próxima acción simple: pedir una foto, pedir medidas, "
    "confirmar una ubicación, o invitar a enviar los datos necesarios para avanzar."
)


def replace_seeded_prompt(apps, schema_editor):
    del schema_editor
    prompt_configuration = apps.get_model("configuration", "PromptConfiguration")
    for configuration in prompt_configuration.objects.all().iterator():
        prompt = configuration.automatic_reply_prompt
        if (
            prompt.startswith(LEGACY_DEFAULT_PREFIX)
            and LEGACY_DEFAULT_MARKER in prompt
            and prompt.endswith(LEGACY_DEFAULT_SUFFIX)
        ):
            configuration.automatic_reply_prompt = STYLE_ONLY_AUTOMATIC_REPLY_PROMPT
            configuration.save(update_fields=("automatic_reply_prompt",))


class Migration(migrations.Migration):
    dependencies = [
        ("configuration", "0016_alter_promptconfiguration_automatic_reply_prompt"),
    ]

    operations = [
        migrations.AlterField(
            model_name="promptconfiguration",
            name="automatic_reply_prompt",
            field=models.TextField(default=STYLE_ONLY_AUTOMATIC_REPLY_PROMPT),
        ),
        migrations.RunPython(replace_seeded_prompt, migrations.RunPython.noop),
    ]
