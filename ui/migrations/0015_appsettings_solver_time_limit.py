"""Add solver_time_limit to AppSettings."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0014_job_remove_item"),
    ]

    operations = [
        migrations.AddField(
            model_name="appsettings",
            name="solver_time_limit",
            field=models.FloatField(
                default=10.0,
                help_text="Timeout massimo del solver di ottimizzazione (secondi).",
            ),
        ),
    ]
