from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("configuration", "0021_refresh_default_search_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="integrationconfiguration",
            name="relevance_llm_provider",
            field=models.CharField(
                blank=True,
                choices=[
                    ("fake", "Mock (sin red)"),
                    ("ollama", "Ollama"),
                    ("openai-compatible", "OpenAI compatible"),
                ],
                default="",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="integrationconfiguration",
            name="relevance_llm_base_url",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="integrationconfiguration",
            name="relevance_llm_api_key_encrypted",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="integrationconfiguration",
            name="relevance_llm_api_key_source",
            field=models.CharField(
                choices=[
                    ("ENVIRONMENT", "Entorno"),
                    ("ENCRYPTED", "Dashboard cifrado"),
                    ("NONE", "Sin configurar"),
                ],
                default="NONE",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="integrationconfiguration",
            constraint=models.CheckConstraint(
                condition=models.Q(("relevance_llm_api_key_source", "ENCRYPTED"), _negated=True)
                | models.Q(("relevance_llm_api_key_encrypted", ""), _negated=True),
                name="integration_relevance_llm_cipher_required",
            ),
        ),
    ]
