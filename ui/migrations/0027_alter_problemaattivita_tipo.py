# Generated manually: add manutenzione problem type for auto-swap notes.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0026_testa_generatore_in_manutenzione"),
    ]

    operations = [
        migrations.AlterField(
            model_name="problemaattivita",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("spostamento", "Spostamento richiesto"),
                    ("acquisto", "Risorsa da recuperare"),
                    ("sovrapposizione", "Sovrapposizione risorse"),
                    ("manutenzione", "Sostituzione per manutenzione"),
                ],
                max_length=20,
            ),
        ),
    ]
