"""Finalize: the Job link is mandatory, the predecessor text column and the
never-used ``risoluzione_problema`` table go away."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0036_link_jobs_and_predecessori"),
    ]

    operations = [
        migrations.AlterField(
            model_name="attivita",
            name="job",
            field=models.ForeignKey(
                db_column="job_id",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="attivita",
                to="ui.job",
            ),
        ),
        migrations.RemoveField(
            model_name="attivita",
            name="predecessors",
        ),
        migrations.DeleteModel(
            name="RisoluzioneProblema",
        ),
    ]
