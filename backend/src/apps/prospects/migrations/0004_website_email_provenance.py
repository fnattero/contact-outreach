from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prospects", "0003_aianalysis_generation_aianalysis_next_retry_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="prospectemail",
            name="source_content_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="prospectemail",
            name="source_url",
            field=models.URLField(blank=True, max_length=1000),
        ),
        migrations.AddField(
            model_name="websitesnapshot",
            name="email_candidates",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
