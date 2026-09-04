"""Role-based access control for the three permission layers of the app.

The application distinguishes three layers, stored on
:attr:`ui.models.UserProfile.ruolo`:

``admin``
    Full write access **and** access to the Django admin panel, where user
    permissions are assigned.  Mapped onto ``User.is_staff`` /
    ``User.is_superuser`` by :func:`sync_user_flags`.
``scrittura``
    Can view everything and can create/modify commesse, attività, inventory
    and settings.  No admin panel.
``lettura``
    Read-only.  Every mutating endpoint is refused with HTTP 403.

Views declare their requirement with the :func:`write_required` and
:func:`admin_required` decorators.  API endpoints (paths under ``/api/``)
receive a JSON error body so the front-end can surface it; page views get a
plain 403, or a redirect to the login page when the user is anonymous.
"""

from __future__ import annotations

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse, JsonResponse

WRITE_DENIED_MESSAGE = (
    "Permessi insufficienti: il tuo account ha accesso in sola lettura."
)
ADMIN_DENIED_MESSAGE = (
    "Permessi insufficienti: questa operazione richiede il ruolo amministratore."
)


def get_ruolo(user) -> str:
    """Return the permission layer of ``user``.

    Args:
        user: A :class:`~django.contrib.auth.models.User`, ``AnonymousUser``
            or ``None``.

    Returns:
        str: One of ``admin``, ``scrittura``, ``lettura``; empty string for
            anonymous or missing users.
    """
    from ui.models import UserProfile

    if user is None or not getattr(user, "is_authenticated", False):
        return ""
    if user.is_superuser:
        return UserProfile.RUOLO_ADMIN
    profile = getattr(user, "profile", None)
    if profile is None:
        return UserProfile.RUOLO_LETTURA
    return profile.ruolo


def has_write_access(user) -> bool:
    """Return ``True`` when ``user`` may modify application data."""
    from ui.models import UserProfile

    return get_ruolo(user) in {UserProfile.RUOLO_SCRITTURA, UserProfile.RUOLO_ADMIN}


def is_admin_user(user) -> bool:
    """Return ``True`` when ``user`` may reach the admin panel."""
    from ui.models import UserProfile

    return get_ruolo(user) == UserProfile.RUOLO_ADMIN


def get_or_create_profile(user):
    """Return the profile of ``user``, creating it with a default role.

    Users created outside the registration form (``createsuperuser``,
    fixtures, the admin panel) may have no profile row yet.  Superusers get
    the ``admin`` role, everyone else starts read-only.

    Args:
        user: The user whose profile is required.

    Returns:
        UserProfile: The existing or newly created profile.
    """
    from ui.models import UserProfile

    ruolo = UserProfile.RUOLO_ADMIN if user.is_superuser else UserProfile.RUOLO_LETTURA
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"ruolo": ruolo})
    return profile


def sync_user_flags(profile) -> bool:
    """Align ``User.is_staff`` / ``User.is_superuser`` with the profile role.

    Only the ``admin`` layer may open the Django admin panel, so the staff
    and superuser flags mirror :attr:`UserProfile.is_admin`.  The update is
    issued through the queryset API so it does not re-trigger the ``User``
    ``post_save`` handler.

    Args:
        profile: The :class:`~ui.models.UserProfile` that was just saved.

    Returns:
        bool: ``True`` when the user row had to be updated.
    """
    from django.contrib.auth.models import User

    user = profile.user
    is_admin = profile.is_admin
    if user.is_staff == is_admin and user.is_superuser == is_admin:
        return False

    user.is_staff = is_admin
    user.is_superuser = is_admin
    User.objects.filter(pk=user.pk).update(is_staff=is_admin, is_superuser=is_admin)
    return True


def _wants_json(request: HttpRequest) -> bool:
    """Return ``True`` when the caller expects a JSON error body."""
    return (
        request.path.startswith("/api/")
        or request.headers.get("x-requested-with") == "XMLHttpRequest"
    )


def _denied(request: HttpRequest, message: str) -> HttpResponse:
    """Build the 403 response matching the request type."""
    if _wants_json(request):
        return JsonResponse({"ok": False, "error": message}, status=403)
    return HttpResponse(message, content_type="text/plain; charset=utf-8", status=403)


def _unauthenticated(request: HttpRequest) -> HttpResponse:
    """Refuse an anonymous caller: JSON 403 for APIs, login redirect otherwise."""
    if _wants_json(request):
        return JsonResponse(
            {"ok": False, "error": "Autenticazione richiesta."}, status=403
        )
    return redirect_to_login(request.get_full_path())


def deny_if_read_only(request: HttpRequest) -> HttpResponse | None:
    """Return a 403 response when the caller may not write, else ``None``.

    Useful for endpoints that serve both a read and a write method and can
    therefore not be wrapped by :func:`write_required` as a whole.

    Args:
        request: The incoming HTTP request.

    Returns:
        HttpResponse | None: The refusal to return to the client, or ``None``
            when the request may proceed.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return _unauthenticated(request)
    if not has_write_access(user):
        return _denied(request, WRITE_DENIED_MESSAGE)
    return None


def _guard(check, message):
    """Build a view decorator enforcing ``check`` on ``request.user``."""

    def decorator(view):
        @wraps(view)
        def wrapper(request: HttpRequest, *args, **kwargs):
            user = getattr(request, "user", None)
            if user is None or not user.is_authenticated:
                return _unauthenticated(request)
            if not check(user):
                return _denied(request, message)
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


write_required = _guard(has_write_access, WRITE_DENIED_MESSAGE)
"""Decorator refusing users without the ``scrittura`` or ``admin`` layer."""

admin_required = _guard(is_admin_user, ADMIN_DENIED_MESSAGE)
"""Decorator refusing users without the ``admin`` layer."""
