"""Shared helpers for test users and for equipping activities with resources."""

from __future__ import annotations

from django.contrib.auth.models import User

from ui.models import RisorsaAttivita, UserProfile

#: One resource per category: the default minimum kit an activity needs before
#: it can be started (see
#: :func:`ui.services.problem_detection.missing_minimum_resources`).
MINIMUM_KIT = (
    ("testa", "T-MIN"),
    ("mandrino", "M-MIN"),
    ("tastatore", "TA-MIN"),
    ("maschera", "MA-MIN"),
    ("generatore", "G-MIN"),
)


def assign_minimum_kit(attivita, suffix: str = "") -> list[RisorsaAttivita]:
    """Book one resource of every category so *attivita* may be started.

    Args:
        attivita: The :class:`~ui.models.Attivita` to equip.
        suffix: Appended to every UID so two overlapping activities can hold
            distinct kits instead of conflicting on the same resources.

    Returns:
        list[RisorsaAttivita]: The created assignment rows.
    """
    return [
        RisorsaAttivita.objects.create(
            attivita=attivita, tipo=tipo, risorsa_id=f"{uid}{suffix}"
        )
        for tipo, uid in MINIMUM_KIT
    ]


def set_ruolo(user: User, ruolo: str) -> User:
    """Assign a permission layer to an existing user.

    Args:
        user: The user to update.
        ruolo: One of :attr:`UserProfile.RUOLO_CHOICES` keys.

    Returns:
        User: The same user, reloaded so ``is_staff`` / ``is_superuser``
            reflect the role synchronisation.
    """
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.ruolo = ruolo
    profile.save()
    user.refresh_from_db()
    return user


def create_user(
    username: str,
    *,
    password: str = "pass",
    ruolo: str = UserProfile.RUOLO_SCRITTURA,
    **extra,
) -> User:
    """Create a user carrying ``ruolo`` (write access by default).

    Args:
        username: Login name of the new account.
        password: Plain-text password.
        ruolo: Permission layer to assign.
        **extra: Extra fields forwarded to ``User.objects.create_user``.

    Returns:
        User: The created user.
    """
    return set_ruolo(
        User.objects.create_user(username, password=password, **extra), ruolo
    )


def create_reader(username: str = "lettore", **kwargs) -> User:
    """Create a read-only user."""
    return create_user(username, ruolo=UserProfile.RUOLO_LETTURA, **kwargs)


def create_writer(username: str = "scrittore", **kwargs) -> User:
    """Create a user with write access."""
    return create_user(username, ruolo=UserProfile.RUOLO_SCRITTURA, **kwargs)


def create_admin(username: str = "amministratore", **kwargs) -> User:
    """Create a user with the admin permission layer."""
    return create_user(username, ruolo=UserProfile.RUOLO_ADMIN, **kwargs)


def write_user(username: str = "test-writer") -> User:
    """Return a write-access user, creating it on first use.

    Handy for :class:`~django.test.RequestFactory` requests, which need a
    ``request.user`` carrying write permission for the guarded views.

    Args:
        username: Login name of the shared test account.

    Returns:
        User: The existing or newly created user.
    """
    user = User.objects.filter(username=username).first()
    return user if user is not None else create_writer(username)
