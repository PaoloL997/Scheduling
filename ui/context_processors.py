"""Custom template context processors for the ui app."""

from django.http import HttpRequest


def _count_open_problems() -> int:
    """Return unresolved operational problems, including unresolvable overlaps."""
    from ui.models import ProblemaAttivita

    return ProblemaAttivita.objects.filter(risolto=False).count()


def open_problems(request: HttpRequest) -> dict:
    """Inject global open-problems count into every rendered template.

    Args:
        request: The current HTTP request.

    Returns:
        dict: ``{"global_open_problems": int}`` — unresolved problems
            (acquisto, spostamento, and overlap without a substitute).
    """
    from ui.services.runtime_config import load_runtime_config

    runtime = load_runtime_config()
    total = _count_open_problems()

    return {
        "global_open_problems": total,
        "heartbeat_interval_ms": runtime.heartbeat_interval_seconds * 1000,
        "heartbeat_timeout_ms": runtime.heartbeat_timeout_seconds * 1000,
    }
