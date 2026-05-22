"""Django AppConfig for the ui application."""

import logging
import os

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class UiConfig(AppConfig):
    """Application configuration that triggers the background import on startup.

    The import thread is launched exactly once from :meth:`ready`, guarded
    by the ``RUN_MAIN`` environment variable so it does not run twice under
    Django's auto-reloader.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "ui"

    def ready(self) -> None:
        """Start the import pipeline configuration and register authentication signals.

        Under Django's ``runserver`` auto-reloader the process starts twice:
        once as the watcher (``RUN_MAIN`` unset) and once as the actual worker
        (``RUN_MAIN=true``).  The import is launched only in the worker so the
        thread is not duplicated.  In production deployments ``RUN_MAIN`` is
        never set, so the check does not prevent the import from running.
        """
        import sys

        from django.conf import settings

        if "runserver" in sys.argv and os.environ.get("RUN_MAIN") != "true":
            return

        from ui.services.importer import configure

        configure(
            mpp_path=settings.MPP_PATH,
            xlsx_path=settings.XLSX_PATH,
        )

        from ui import signals

        signals.connect()
