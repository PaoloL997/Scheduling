"""Django signal handlers for session tracking, .mpp lock management and roles.

Call :func:`connect` once from :meth:`~ui.apps.UiConfig.ready` to wire up the
``user_logged_in`` and ``user_logged_out`` auth signals, plus the ``post_save``
handlers that keep every account paired with a
:class:`~ui.models.UserProfile` and its permission layer.

The import lifecycle (reset + reimport) is driven entirely by the
:class:`~ui.services.project_session.ProjectSession` background thread, which
detects a ``NOT_STARTED`` status and triggers a fresh import automatically.
These signal handlers therefore only need to register / unregister sessions.
"""

import logging

logger = logging.getLogger(__name__)


def on_user_logged_in(sender, request, user, **kwargs) -> None:
    """Handle a successful user login.

    Registers the session with :class:`~ui.services.project_session.ProjectSession`
    so the COM lock thread is started (or confirmed active) for this session.
    The import lifecycle — clearing stale ORM data and triggering a fresh
    import from the ``.mpp`` file — is handled automatically by the background
    thread when it detects ``ImportStatus.NOT_STARTED``.

    Args:
        sender: The authentication backend class (unused).
        request: The HTTP request that triggered the login.
        user: The authenticated :class:`~django.contrib.auth.models.User`.
    """
    from ui.services.project_session import project_session

    session_key = request.session.session_key if request and request.session else None
    if session_key:
        project_session.register_session(session_key)


def on_user_logged_out(sender, request, user, **kwargs) -> None:
    """Handle an explicit user logout.

    Removes the session from the active set.  When the last session is removed
    the :class:`~ui.services.project_session.ProjectSession` initiates
    write-back and releases the COM lock immediately, without waiting for the
    heartbeat watchdog.

    Args:
        sender: The authentication backend class (unused).
        request: The HTTP request that triggered the logout.
        user: The user who logged out (may be ``None`` in some edge cases).
    """
    from ui.services.project_session import project_session

    session_key = request.session.session_key if request and request.session else None
    if session_key:
        project_session.unregister_session(session_key)


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
    if not created:
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
    from ui.permissions import sync_user_flags

    sync_user_flags(instance)


def connect() -> None:
    """Connect the auth and profile signal handlers.

    Must be called from :meth:`~ui.apps.UiConfig.ready` so that the handlers
    are registered before any requests are processed.
    """
    from django.contrib.auth.models import User
    from django.contrib.auth.signals import user_logged_in, user_logged_out
    from django.db.models.signals import post_save

    from ui.models import UserProfile

    user_logged_in.connect(on_user_logged_in)
    user_logged_out.connect(on_user_logged_out)
    post_save.connect(on_user_saved, sender=User, dispatch_uid="ui.user_profile")
    post_save.connect(
        on_profile_saved, sender=UserProfile, dispatch_uid="ui.profile_flags"
    )
    logger.debug("Segnali di autenticazione e permessi registrati.")
