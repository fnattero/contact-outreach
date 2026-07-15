from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("campaigns", "0002_searchrun_providerusage_and_more"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="campaign",
            constraint=models.CheckConstraint(
                condition=~models.Q(extractor_provider="outscraper")
                | models.Q(cost_currency="USD"),
                name="campaign_outscraper_cost_usd",
            ),
        ),
    ]
