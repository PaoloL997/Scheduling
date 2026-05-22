"""Migration: add RisoluzioneProblema model for persistent resolved-problem notes."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the RisoluzioneProblema table."""

    dependencies = [
        ("ui", "0008_problemaattivita"),
    ]

    operations = [
        migrations.CreateModel(
            name="RisoluzioneProblema",
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
                ("commessa", models.CharField(max_length=100)),
                ("item", models.IntegerField()),
                ("activity", models.CharField(blank=True, default="", max_length=200)),
                ("tipo", models.CharField(max_length=20)),
                ("risorsa_tipo", models.CharField(max_length=20)),
                (
                    "risorsa_uid",
                    models.CharField(blank=True, default="", max_length=100),
                ),
                ("note_risoluzione", models.TextField(blank=True, default="")),
                ("data_risoluzione", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "risoluzione_problema",
                "constraints": [
                    models.UniqueConstraint(
                        fields=[
                            "commessa",
                            "item",
                            "activity",
                            "tipo",
                            "risorsa_tipo",
                            "risorsa_uid",
                        ],
                        name="uq_risoluzione_problema_identity",
                    )
                ],
            },
        ),
    ]
