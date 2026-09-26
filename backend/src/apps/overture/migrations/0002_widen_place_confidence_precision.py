from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("overture", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="overtureplace",
            name="confidence",
            field=models.DecimalField(
                blank=True,
                decimal_places=8,
                max_digits=9,
                null=True,
            ),
        ),
    ]
