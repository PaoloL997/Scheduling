"""Migration: add ProblemaAttivita model."""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the ProblemaAttivita table to track operational problems."""

    dependencies = [
        ("ui", "0007_configurazionetesta_min_generatori"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProblemaAttivita",
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
                    "attivita",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="problemi",
                        to="ui.attivita",
                    ),
                ),
                (
                    "tipo",
                    models.CharField(
                        choices=[
                            ("spostamento", "Spostamento richiesto"),
                            ("acquisto", "Risorsa da recuperare"),
                        ],
                        max_length=20,
                    ),
                ),
                ("risorsa_tipo", models.CharField(max_length=20)),
                (
                    "risorsa_uid",
                    models.CharField(blank=True, default="", max_length=100),
                ),
                (
                    "stabilimento_origine",
                    models.CharField(blank=True, default="", max_length=200),
                ),
                ("descrizione", models.TextField()),
                ("risolto", models.BooleanField(default=False)),
                ("note_risoluzione", models.TextField(blank=True, default="")),
                ("data_risoluzione", models.DateTimeField(blank=True, null=True)),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
            ],
            options={
                "db_table": "problema_attivita",
                "ordering": ["created_at"],
            },
        ),
    ]
