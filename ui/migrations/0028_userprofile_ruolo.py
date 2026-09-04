from django.db import migrations, models


def seed_ruoli(apps, schema_editor):
    """Give existing accounts the role matching the access they already had.

    Before this migration every authenticated user could write, so existing
    profiles are promoted to ``scrittura``; Django superusers become ``admin``.
    Users without a profile row get one so the admin panel can edit them.
    """
    UserProfile = apps.get_model("ui", "UserProfile")
    User = apps.get_model("auth", "User")

    UserProfile.objects.update(ruolo="scrittura")
    UserProfile.objects.filter(user__is_superuser=True).update(ruolo="admin")

    existing = set(UserProfile.objects.values_list("user_id", flat=True))
    UserProfile.objects.bulk_create(
        [
            UserProfile(
                user_id=user.id,
                ruolo="admin" if user.is_superuser else "scrittura",
            )
            for user in User.objects.exclude(id__in=existing)
        ]
    )


def unseed_ruoli(apps, schema_editor):
    """No-op reverse: the column is dropped by the schema operation."""


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0027_alter_problemaattivita_tipo"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="ruolo",
            field=models.CharField(
                choices=[
                    ("lettura", "Lettura"),
                    ("scrittura", "Scrittura"),
                    ("admin", "Admin"),
                ],
                default="lettura",
                max_length=20,
            ),
        ),
        migrations.RunPython(seed_ruoli, unseed_ruoli),
    ]
