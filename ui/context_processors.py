"""Custom template context processors for the ui app."""

from django.http import HttpRequest


def open_problems(request: HttpRequest) -> dict:
    """Inject global open-problem count into every rendered template.

    Args:
        request: The current HTTP request.

    Returns:
        dict: ``{"global_open_problems": int}`` — count of unresolved
            :class:`~ui.models.ProblemaAttivita` records.
    """
    from ui.models import ProblemaAttivita

    return {
        "global_open_problems": ProblemaAttivita.objects.filter(risolto=False).count()
    }
