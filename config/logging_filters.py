"""Custom logging filters for the programmazione_produzione project."""

import logging


class SuppressStatusEndpoint(logging.Filter):
    """Suppress HTTP log entries for the /api/status polling endpoint.

    The status endpoint is polled frequently by the frontend while the
    database import is in progress.  Without this filter those repeated
    GET requests flood the console and obscure meaningful log output.

    This filter matches both the ``django.server`` logger (used by
    ``runserver``) and the ``django.request`` logger (used in production
    WSGI/ASGI deployments) by inspecting the fully-formatted log message.
    """

    _SUPPRESSED_PATH = "/api/status"

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False for any log record that references the status endpoint.

        Args:
            record: The log record to evaluate.

        Returns:
            bool: False if the record's formatted message contains
                ``/api/status``; True otherwise (record is kept).
        """
        return self._SUPPRESSED_PATH not in record.getMessage()
