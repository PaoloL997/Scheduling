"""Django signal handlers for login/logout session tracking and .mpp lock management.

Call :func:`connect` once from :meth:`~ui.apps.UiConfig.ready` to wire up the
``user_logged_in`` and ``user_logged_out`` auth signals.

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


def connect() -> None:
    """Connect :func:`on_user_logged_in` and :func:`on_user_logged_out` to the auth signals.

    Must be called from :meth:`~ui.apps.UiConfig.ready` so that the handlers
    are registered before any requests are processed.
    """
    from django.contrib.auth.signals import user_logged_in, user_logged_out

    user_logged_in.connect(on_user_logged_in)
    user_logged_out.connect(on_user_logged_out)
    logger.debug("Segnali di autenticazione registrati.")
