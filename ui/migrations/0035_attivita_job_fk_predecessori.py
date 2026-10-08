"""Schema: ``attivita.job_id`` becomes a foreign key to ``job`` and the
predecessors become a many-to-many relation.

The ``job_id`` column keeps its name and its values (``build_job_id`` of the
commessa, which is the Job primary key); it is dropped and re-added so the
constraint can be attached, and refilled by the next migration.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0034_create_missing_jobs"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="attivita",
            name="job_id",
        ),
        migrations.AddField(
            model_name="attivita",
            name="job",
            field=models.ForeignKey(
                db_column="job_id",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="attivita",
                to="ui.job",
            ),
        ),
        migrations.AddField(
            model_name="attivita",
            name="predecessori",
            field=models.ManyToManyField(
                blank=True,
                related_name="successori",
                to="ui.attivita",
            ),
        ),
    ]
