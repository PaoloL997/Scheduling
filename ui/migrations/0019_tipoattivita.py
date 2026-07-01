"""Migration: add TipoAttivita reference table for configurable activity names."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0018_appsettings_auto_import_on_startup_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="TipoAttivita",
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
                ("nome", models.CharField(max_length=200, unique=True)),
            ],
            options={
                "db_table": "tipo_attivita",
                "ordering": ["nome"],
            },
        ),
    ]
