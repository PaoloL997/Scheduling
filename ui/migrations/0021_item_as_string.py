from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0020_delete_appsettings"),
    ]

    operations = [
        migrations.AlterField(
            model_name="attivita",
            name="item",
            field=models.CharField(default="1", max_length=50),
        ),
        migrations.AlterField(
            model_name="risoluzioneproblema",
            name="item",
            field=models.CharField(default="1", max_length=50),
        ),
    ]
