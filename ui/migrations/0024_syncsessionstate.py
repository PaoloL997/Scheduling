# Generated manually for SyncSessionState

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0023_merge_20260728_0935"),
    ]

    operations = [
        migrations.CreateModel(
            name="SyncSessionState",
            fields=[
                (
                    "singleton_id",
                    models.PositiveSmallIntegerField(
                        default=1, primary_key=True, serialize=False
                    ),
                ),
                (
                    "phase",
                    models.CharField(
                        choices=[
                            ("idle", "Idle"),
                            ("loading", "Loading"),
                            ("ready", "Ready"),
                            ("writing", "Writing"),
                            ("dirty", "Dirty"),
                        ],
                        default="idle",
                        max_length=20,
                    ),
                ),
                ("last_error", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "sync_session_state",
            },
        ),
    ]
