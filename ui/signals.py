"""Django signal handlers for user roles.

Call :func:`connect` once from :meth:`~ui.apps.UiConfig.ready` to wire up the
``post_save`` handlers that keep every account paired with a
:class:`~ui.models.UserProfile` and its permission layer.
"""

import logging

logger = logging.getLogger(__name__)


def on_user_saved(sender, instance, created, **kwargs) -> None:
    """Create the profile of a brand-new account with its default role.

    Accounts created outside the registration form (``createsuperuser``, the
    admin panel, tests) would otherwise have no
    :class:`~ui.models.UserProfile`, and therefore no permission layer to
    edit.  Superusers start as ``admin``, everyone else as ``lettura``.

    Args:
        sender: The :class:`~django.contrib.auth.models.User` model class.
        instance: The user that was just saved.
        created: Whether the row was inserted rather than updated.
    """
    # Fixture loading (``loaddata``) brings its own profiles along.
    if not created or kwargs.get("raw"):
        return

    from ui.permissions import get_or_create_profile

    get_or_create_profile(instance)


def on_profile_saved(sender, instance, **kwargs) -> None:
    """Mirror the profile role onto the Django staff / superuser flags.

    Only the ``admin`` layer may open the Django admin panel, so the flags
    follow :attr:`~ui.models.UserProfile.ruolo` whenever a profile is saved
    — including from the admin panel itself.

    Args:
        sender: The :class:`~ui.models.UserProfile` model class.
        instance: The profile that was just saved.
    """
    # Fixture loading (``loaddata``) restores the flags exactly as dumped.
    if kwargs.get("raw"):
        return

    from ui.permissions import sync_user_flags

    sync_user_flags(instance)


def connect() -> None:
    """Connect the profile signal handlers.

    Must be called from :meth:`~ui.apps.UiConfig.ready` so that the handlers
    are registered before any requests are processed.
    """
    from django.contrib.auth.models import User
    from django.db.models.signals import post_save

    from ui.models import UserProfile

    post_save.connect(on_user_saved, sender=User, dispatch_uid="ui.user_profile")
    post_save.connect(
        on_profile_saved, sender=UserProfile, dispatch_uid="ui.profile_flags"
    )
    logger.debug("Segnali dei profili utente registrati.")
