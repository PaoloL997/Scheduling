"""Django AppConfig for the ui application."""

from django.apps import AppConfig


class UiConfig(AppConfig):
    """Application configuration for the ui app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "ui"

    def ready(self) -> None:
        """Register the account and profile signal handlers."""
        from ui import signals

        signals.connect()
