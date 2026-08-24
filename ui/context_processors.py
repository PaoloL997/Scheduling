"""Custom template context processors for the ui app."""

from django.http import HttpRequest


def _count_open_problems() -> int:
    """Return open problems plus unique delay-overlap pairs."""
    from ui.models import ProblemaAttivita
    from ui.services.problem_detection import find_resource_overlaps

    problems = (
        ProblemaAttivita.objects.filter(risolto=False)
        .exclude(tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE)
        .count()
    )
    overlaps = find_resource_overlaps()
    pairs = sum(
        1 for items in overlaps.values() for item in items if item.get("role") == "late"
    )
    return problems + pairs


def open_problems(request: HttpRequest) -> dict:
    """Inject global open-problems count into every rendered template.

    Args:
        request: The current HTTP request.

    Returns:
        dict: ``{"global_open_problems": int}`` — unresolved problems
            (acquisto/spostamento) plus unique resource-overlap pairs.
    """
    from ui.services.runtime_config import load_runtime_config

    runtime = load_runtime_config()
    total = _count_open_problems()

    return {
        "global_open_problems": total,
        "heartbeat_interval_ms": runtime.heartbeat_interval_seconds * 1000,
        "heartbeat_timeout_ms": runtime.heartbeat_timeout_seconds * 1000,
    }
