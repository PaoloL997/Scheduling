"""Every commessa named by an activity gets its Job row.

Until now an activity was tied to its commessa by a string, and a commessa
could exist only in the activity table.  The foreign key that follows needs
one Job per commessa.
"""

from django.db import migrations

from ui.services.identifiers import build_job_id


def create_missing_jobs(apps, schema_editor):
    Attivita = apps.get_model("ui", "Attivita")
    Job = apps.get_model("ui", "Job")

    existing = set(Job.objects.values_list("commessa", flat=True))
    named = set(
        Attivita.objects.exclude(commessa="")
        .values_list("commessa", flat=True)
        .distinct()
    )
    missing = sorted(named - existing)
    Job.objects.bulk_create([Job(job_id=build_job_id(c), commessa=c) for c in missing])
    if missing:
        print(f"\n  Job creati per commesse senza riga: {', '.join(missing)}")


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0033_remove_mpp_sync"),
    ]

    operations = [
        migrations.RunPython(create_missing_jobs, migrations.RunPython.noop),
    ]
