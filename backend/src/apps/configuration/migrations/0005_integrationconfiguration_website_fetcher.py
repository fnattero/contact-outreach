from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("configuration", "0004_promptconfiguration"),
    ]

    operations = [
        migrations.AddField(
            model_name="integrationconfiguration",
            name="website_fetcher",
            field=models.CharField(
                choices=[("fake", "Mock (sin red)"), ("http", "HTTP real seguro")],
                default="fake",
                max_length=20,
            ),
        ),
    ]
