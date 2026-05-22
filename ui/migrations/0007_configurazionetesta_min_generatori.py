"""Add min_generatori field to ConfigurazioneTesta."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add min_generatori to ConfigurazioneTesta with default value 1."""

    dependencies = [
        ("ui", "0006_extend_attivita_drop_task"),
    ]

    operations = [
        migrations.AddField(
            model_name="configurazionetesta",
            name="min_generatori",
            field=models.PositiveIntegerField(default=1),
        ),
    ]
