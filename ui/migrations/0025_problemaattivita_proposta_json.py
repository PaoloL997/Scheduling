# Generated manually for ProblemaAttivita.proposta_json

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0024_syncsessionstate"),
    ]

    operations = [
        migrations.AddField(
            model_name="problemaattivita",
            name="proposta_json",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
