"""Migration: add AppSettings singleton model for app-wide configuration."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the AppSettings table."""

    dependencies = [
        ("ui", "0009_risoluzione_problema"),
    ]

    operations = [
        migrations.CreateModel(
            name="AppSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "penale_max",
                    models.DecimalField(
                        decimal_places=2,
                        default=5,
                        help_text="Percentuale massima di penale applicabile.",
                        max_digits=5,
                    ),
                ),
            ],
            options={
                "db_table": "app_settings",
            },
        ),
    ]
