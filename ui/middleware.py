"""Session tracking middleware for .mpp lock management and write-back protection.

Ensures that sessions which survive a server restart are tracked by
:class:`~ui.services.project_session.ProjectSession`.  Without this,
users whose session cookies were issued before a restart would be
authenticated (Django restores their session from the database) but the
in-memory ``_active_sessions`` set would be empty, so the hold thread
would not be started.

On every authenticated request this middleware calls
:meth:`~ui.services.project_session.ProjectSession.ensure_session`,
which adds the session to the active set (if missing) and starts the hold
thread after a server restart.  It does **not** refresh the heartbeat idle
timer — only explicit ``/api/heartbeat/`` pings do that.

Additionally, while the import status is :attr:`~ui.services.importer.ImportStatus.WRITING`
(write-back in progress), all mutating API requests (``POST``, ``PUT``,
``PATCH``, ``DELETE``) to ``/api/`` endpoints are rejected with HTTP 409
and a JSON error body so that concurrent in-flight edits cannot corrupt
the data being written back.
"""

import json
import logging

from django.http import HttpResponse

logger = logging.getLogger(__name__)

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class SessionTrackingMiddleware:
    """WSGI middleware that registers authenticated sessions with ProjectSession.

    Also blocks mutating API requests while a write-back is in progress.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in _MUTATING_METHODS and request.path.startswith("/api/"):
            try:
                from ui.services.importer import ImportStatus, get_status

                if get_status() == ImportStatus.WRITING:
                    logger.warning(
                        "Richiesta %s %s rifiutata: write-back in corso.",
                        request.method,
                        request.path,
                    )
                    return HttpResponse(
                        json.dumps(
                            {
                                "ok": False,
                                "error": "Scrittura file in corso. Riprovare tra qualche istante.",
                            }
                        ),
                        content_type="application/json",
                        status=409,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Errore nel controllo WRITING in SessionTrackingMiddleware: %s", exc
                )

        response = self.get_response(request)

        if (
            hasattr(request, "user")
            and request.user.is_authenticated
            and hasattr(request, "session")
            and request.session.session_key
        ):
            try:
                from ui.services.project_session import project_session

                project_session.ensure_session(request.session.session_key)
            except Exception as exc:  # noqa: BLE001
                logger.error("Errore in SessionTrackingMiddleware: %s", exc)

        return response
