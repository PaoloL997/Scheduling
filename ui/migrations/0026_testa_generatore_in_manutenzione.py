# Generated manually for TestaItem/GeneratoreItem.in_manutenzione

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0025_problemaattivita_proposta_json"),
    ]

    operations = [
        migrations.AddField(
            model_name="testaitem",
            name="in_manutenzione",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="generatoreitem",
            name="in_manutenzione",
            field=models.BooleanField(default=False),
        ),
    ]
