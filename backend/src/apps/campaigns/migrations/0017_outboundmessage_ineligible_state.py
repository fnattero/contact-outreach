from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("campaigns", "0016_backfill_attachment_collections")]

    operations = [
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
                    ("INELIGIBLE", "Ya no se puede enviar"),
                    ("SEND_FAILED", "Falló"),
                    ("CANCELLED", "Cancelado"),
                ],
                default="PREPARED",
                max_length=30,
            ),
        ),
    ]
