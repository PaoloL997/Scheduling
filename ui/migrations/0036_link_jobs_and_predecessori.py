"""Data: link every activity to its Job and turn the predecessor text into
relation rows.

Predecessor references that name no activity of the same commessa are
reported and dropped: they were already dangling.
"""

from django.db import migrations

from ui.services.identifiers import build_job_id, normalize_activity_id


def link_jobs(apps, schema_editor):
    Attivita = apps.get_model("ui", "Attivita")
    Job = apps.get_model("ui", "Job")
    job_ids = set(Job.objects.values_list("job_id", flat=True))
    for commessa in Attivita.objects.values_list("commessa", flat=True).distinct():
        job_id = build_job_id(commessa)
        if job_id not in job_ids:
            job = Job.objects.create(job_id=job_id, commessa=commessa)
            job_ids.add(job.job_id)
        Attivita.objects.filter(commessa=commessa).update(job_id=job_id)


def link_predecessori(apps, schema_editor):
    Attivita = apps.get_model("ui", "Attivita")
    rows = list(
        Attivita.objects.exclude(predecessors__isnull=True).exclude(predecessors="")
    )
    if not rows:
        return
    by_commessa: dict[str, dict[str, object]] = {}
    for att in Attivita.objects.all():
        by_commessa.setdefault(att.commessa, {})[
            normalize_activity_id(att.activity_id)
        ] = att

    unresolved: list[str] = []
    for att in rows:
        known = by_commessa.get(att.commessa, {})
        targets = []
        for token in att.predecessors.split(","):
            token = token.strip()
            if not token:
                continue
            pred = known.get(normalize_activity_id(token))
            if pred is None or pred.pk == att.pk:
                unresolved.append(f"{att.activity_id} -> {token}")
                continue
            if pred not in targets:
                targets.append(pred)
        if targets:
            att.predecessori.set(targets)
    if unresolved:
        print("\n  Riferimenti a predecessori non risolti (scartati):")
        for line in unresolved:
            print(f"    {line}")


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0035_attivita_job_fk_predecessori"),
    ]

    operations = [
        migrations.RunPython(link_jobs, migrations.RunPython.noop),
        migrations.RunPython(link_predecessori, migrations.RunPython.noop),
    ]
