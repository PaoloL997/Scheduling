"""Migration: add sovrapposizione problem type and overlap-note fields."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add ``bloccata_fino`` and ``riferimento_attivita`` to ProblemaAttivita."""

    dependencies = [
        ("ui", "0020_delete_appsettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="problemaattivita",
            name="bloccata_fino",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="problemaattivita",
            name="riferimento_attivita",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
    ]
