"""Seed the AppSettings singleton row with default values."""

from decimal import Decimal

from django.db import migrations


def seed_appsettings(apps, schema_editor):
    AppSettings = apps.get_model("ui", "AppSettings")
    AppSettings.objects.get_or_create(
        pk=1,
        defaults={
            "penale_max": Decimal("5"),
            "solver_time_limit": 10.0,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0015_appsettings_solver_time_limit"),
    ]

    operations = [
        migrations.RunPython(seed_appsettings, migrations.RunPython.noop),
    ]
