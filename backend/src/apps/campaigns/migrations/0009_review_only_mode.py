from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("campaigns", "0008_outboundmessage_manual_reply_parent_inbound_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="campaign",
            name="website_fetcher",
            field=models.CharField(default="fake", max_length=20),
        ),
        migrations.AlterField(
            model_name="campaign",
            name="delivery_mode",
            field=models.CharField(
                choices=[
                    ("DRY_RUN", "Simulación"),
                    ("REVIEW_ONLY", "Solo revisión (sin Gmail)"),
                    ("LIVE", "En vivo"),
                ],
                default="DRY_RUN",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="outboundmessage",
            name="delivery_mode",
            field=models.CharField(
                choices=[
                    ("DRY_RUN", "Simulación"),
                    ("REVIEW_ONLY", "Solo revisión (sin Gmail)"),
                    ("LIVE", "En vivo"),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="outboundmessage",
            name="state",
            field=models.CharField(
                choices=[
                    ("PREPARED", "Preparado"),
                    ("REVIEW_READY", "Listo para revisar"),
                    ("QUEUED", "En cola"),
                    ("SENDING", "Enviando"),
                    ("RECONCILING", "Reconciliando"),
                    ("SENT", "Enviado"),
                    ("DRY_RUN_COMPLETED", "Simulado"),
                    ("SEND_FAILED", "Falló"),
                    ("CANCELLED", "Cancelado"),
                ],
                default="PREPARED",
                max_length=30,
            ),
        ),
    ]
