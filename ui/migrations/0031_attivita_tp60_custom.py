# Generated manually for Attivita.tp60_custom

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0030_alter_problemaattivita_tipo"),
    ]

    operations = [
        migrations.AddField(
            model_name="attivita",
            name="tp60_custom",
            field=models.BooleanField(default=False),
        ),
    ]
