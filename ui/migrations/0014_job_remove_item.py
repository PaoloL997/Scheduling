"""Remove item field from Job and simplify job_id to encoded commessa only.

Data migration steps:
1. For each commessa group with multiple Job rows (from different items),
   collapse into a single row keeping the first non-null value for each
   commercial field (total_cost, penalty, delivery_date).
2. Update job_id on all Job rows to URL-encoded commessa (no item suffix).
3. Update Attivita.job_id to match the new job_id format (remove item suffix).

Schema migration:
- Remove item column from job table.
- Add unique constraint on commessa.
"""

from __future__ import annotations

from urllib.parse import quote

from django.db import migrations, models


def _enc(value: str) -> str:
    """URL-encode a commessa name for use as a job identifier.

    Args:
        value (str): Raw commessa name.

    Returns:
        str: URL-encoded commessa name.
    """
    return quote((value or "").strip(), safe="")


def collapse_jobs_and_rekey(apps, schema_editor):
    """Collapse multi-item Job rows and rekey job_id to encoded commessa.

    For each commessa that has more than one Job row (different items), keeps
    the first row and merges non-null commercial fields from subsequent rows,
    then deletes the duplicates.  Updates all Job.job_id values and all
    Attivita.job_id values to the new format (encoded commessa only).

    Args:
        apps: Django app registry snapshot for this migration.
        schema_editor: The database schema editor (unused; ORM operations only).
    """
    Job = apps.get_model("ui", "Job")
    Attivita = apps.get_model("ui", "Attivita")

    commessa_to_jobs: dict[str, list] = {}
    for job in Job.objects.order_by("commessa", "item"):
        commessa_to_jobs.setdefault(job.commessa, []).append(job)

    for commessa, jobs in commessa_to_jobs.items():
        new_job_id = _enc(commessa)
        primary = jobs[0]

        for duplicate in jobs[1:]:
            for field in ("total_cost", "penalty", "delivery_date"):
                if getattr(primary, field) in (None, "") and getattr(
                    duplicate, field
                ) not in (None, ""):
                    setattr(primary, field, getattr(duplicate, field))
            duplicate.delete()

        primary.job_id = new_job_id
        primary.save(update_fields=["job_id", "total_cost", "penalty", "delivery_date"])

    Attivita.objects.update(job_id=None)
    for att in Attivita.objects.all().iterator():
        att.job_id = _enc(att.commessa)
        att.save(update_fields=["job_id"])


def reverse_collapse(apps, schema_editor):
    """Reverse migration: no-op because collapsed rows cannot be reconstructed.

    The reverse is intentionally left empty — the item data is not retained
    during the forward migration and cannot be recovered.

    Args:
        apps: Django app registry snapshot.
        schema_editor: The database schema editor.
    """


class Migration(migrations.Migration):
    """Remove item from Job and simplify job_id to commessa-only identifier."""

    dependencies = [
        ("ui", "0013_job_id_primary_key"),
    ]

    operations = [
        migrations.RunPython(collapse_jobs_and_rekey, reverse_collapse),
        migrations.RemoveConstraint(
            model_name="job",
            name="uq_job_commessa_item",
        ),
        migrations.RemoveField(
            model_name="job",
            name="item",
        ),
        migrations.AlterField(
            model_name="job",
            name="commessa",
            field=models.CharField(max_length=100, unique=True),
        ),
    ]
