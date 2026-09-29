"""Custom template context processors for the ui app."""

from django.http import HttpRequest

_RUOLO_LABELS = {
    "admin": "Amministratore",
    "scrittura": "Scrittura",
    "lettura": "Sola lettura",
}


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
    return {"global_open_problems": _count_open_problems()}


def user_permissions(request: HttpRequest) -> dict:
    """Inject the current user's permission layer into every template.

    Templates use ``can_write`` to hide the actions a read-only account is
    not allowed to perform, and ``is_admin_user`` to show the admin-panel
    link.  Server-side enforcement lives in :mod:`ui.permissions`; hiding the
    controls is only a convenience.

    Args:
        request: The current HTTP request.

    Returns:
        dict: ``{"user_ruolo": str, "can_write": bool, "is_admin_user": bool}``.
    """
    from ui.permissions import get_ruolo, has_write_access, is_admin_user

    user = getattr(request, "user", None)
    return {
        "user_ruolo": get_ruolo(user),
        "user_ruolo_label": _RUOLO_LABELS.get(get_ruolo(user), ""),
        "can_write": has_write_access(user),
        "is_admin_user": is_admin_user(user),
    }
