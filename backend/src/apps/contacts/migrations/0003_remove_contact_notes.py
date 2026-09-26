from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("contacts", "0002_backfill_contact_foundation"),
    ]

    operations = [migrations.RemoveField(model_name="contact", name="notes")]
