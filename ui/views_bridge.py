"""JSON bridge API for external devices (tablets) over SQLite."""

from __future__ import annotations

import json
from functools import wraps

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.test import RequestFactory
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ui.permissions import (
    WRITE_DENIED_MESSAGE,
    get_ruolo,
    has_write_access,
    is_admin_user,
)
from ui.services.bridge_import import request_import
from ui.services.bridge_serializers import (
    serialize_app_settings,
    serialize_attivita,
    serialize_attivita_list,
    serialize_config_testa,
    serialize_dataset,
    serialize_inventario,
    serialize_jobs,
    serialize_problemi,
    serialize_responsabili,
    serialize_tipi_attivita,
    serialize_stabilimenti,
)
from ui.services.bridge_write import BridgeWriteError, delete_attivita, update_attivita
from ui.views_auth import user_stabilimento_name
from ui.services.importer import ImportStatus, get_error_detail, get_status
from ui import views
from ui import views_attivita

_factory = RequestFactory()


def _user_payload(user) -> dict:
    """Serialize core user fields and permission layer for bridge auth responses."""
    return {
        "id": user.pk,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "stabilimento": user_stabilimento_name(user),
        "ruolo": get_ruolo(user),
        "can_write": has_write_access(user),
        "is_admin": is_admin_user(user),
    }


def _ok(data=None, *, status: int = 200) -> JsonResponse:
    payload: dict = {"ok": True}
    if data is not None:
        payload["data"] = data
    return JsonResponse(payload, status=status)


def _err(message: str, *, status: int = 400, **extra) -> JsonResponse:
    payload = {"ok": False, "error": message, **extra}
    return JsonResponse(payload, status=status)


def _parse_json(request: HttpRequest) -> dict | JsonResponse:
    try:
        return json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _err("Body JSON non valido.", status=400)


def bridge_read_guard(view):
    """Require authenticated user and READY import status for read endpoints."""

    @wraps(view)
    @login_required
    def wrapper(request: HttpRequest, *args, **kwargs):
        status = get_status()
        if status == ImportStatus.WRITING:
            return _err("Write-back in corso.", status=409)
        if status != ImportStatus.READY:
            return _err("Dataset non pronto.", status=503)
        return view(request, *args, **kwargs)

    return wrapper


def bridge_write_guard(view):
    """Require write permission and READY import status for mutating endpoints.

    Read-only accounts (``lettura`` layer) are refused with HTTP 403 before
    the dataset status is even considered, so the tablet can show a clear
    "sola lettura" message.
    """

    @wraps(view)
    @login_required
    @csrf_exempt
    def wrapper(request: HttpRequest, *args, **kwargs):
        if not has_write_access(request.user):
            return _err(WRITE_DENIED_MESSAGE, status=403)
        status = get_status()
        if status == ImportStatus.WRITING:
            return _err("Write-back in corso.", status=409)
        if status != ImportStatus.READY:
            return _err("Dataset non pronto.", status=503)
        return view(request, *args, **kwargs)

    return wrapper


def _forward_response(response: JsonResponse) -> JsonResponse:
    """Pass through delegated view responses unchanged."""
    return response


def _delegate(
    view_func,
    request: HttpRequest,
    method: str,
    path: str,
    *,
    data: dict | None = None,
    **kwargs,
):
    body = json.dumps(data).encode() if data is not None else b""
    factory_method = getattr(_factory, method.lower())
    inner = factory_method(path, data=body, content_type="application/json")
    inner.user = request.user
    inner.session = request.session
    return view_func(inner, **kwargs)


# ── Auth ──────────────────────────────────────────────────────────────────────


@csrf_exempt
@require_http_methods(["POST"])
def api_bridge_auth_login(request: HttpRequest) -> JsonResponse:
    """Authenticate via JSON and start a Django session."""
    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed

    username = (parsed.get("username") or "").strip()
    password = parsed.get("password") or ""
    if not username or not password:
        return _err("Username e password obbligatori.", status=400)

    user = authenticate(request, username=username, password=password)
    if user is None:
        return _err("Username o password non corretti.", status=401)

    login(request, user)
    return _ok(_user_payload(user))


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def api_bridge_auth_logout(request: HttpRequest) -> JsonResponse:
    """End the current session."""
    session_key = request.session.session_key
    logout(request)
    if session_key:
        from ui.services.project_session import project_session

        project_session.unregister_session(session_key)
    return _ok()


@require_http_methods(["GET"])
@login_required
def api_bridge_auth_me(request: HttpRequest) -> JsonResponse:
    """Return the authenticated user profile."""
    user = request.user
    return _ok(_user_payload(user))


# ── Status / import ───────────────────────────────────────────────────────────


@require_http_methods(["GET"])
@login_required
def api_bridge_status(request: HttpRequest) -> JsonResponse:
    """Return import pipeline status for tablet polling."""
    from ui.services.sync_state import get_last_error, get_phase, pending_queue_count

    status = get_status()
    detail = ""
    if status == ImportStatus.ERROR:
        detail = get_error_detail()
    elif get_phase() == "dirty" or get_last_error():
        detail = get_last_error() or get_error_detail()
    return _ok(
        {
            "status": status.value,
            "detail": detail,
            "pending_queue_count": pending_queue_count(),
            "sync_phase": get_phase(),
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def api_bridge_import(request: HttpRequest) -> JsonResponse:
    """Trigger a fresh MPP/XLSX import.

    Re-importing replaces the working dataset, so it is reserved to accounts
    with write permission.
    """
    if not has_write_access(request.user):
        return _err(WRITE_DENIED_MESSAGE, status=403)

    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed

    force = bool(parsed.get("force", False))
    result = request_import(force=force)
    if not result["accepted"]:
        return JsonResponse({"ok": False, **result}, status=409)
    return JsonResponse({"ok": True, **result}, status=202)


# ── Read ──────────────────────────────────────────────────────────────────────


@bridge_read_guard
@require_http_methods(["GET"])
def api_bridge_dataset(request: HttpRequest) -> JsonResponse:
    return _ok(serialize_dataset(user_stabilimento_name(request.user)))


@bridge_read_guard
@require_http_methods(["GET"])
def api_bridge_attivita_list(request: HttpRequest) -> JsonResponse:
    return _ok(serialize_attivita_list(user_stabilimento_name(request.user)))


@bridge_read_guard
@require_http_methods(["GET"])
def api_bridge_attivita_detail(request: HttpRequest, pk: int) -> JsonResponse:
    data = serialize_attivita(pk)
    if data is None:
        return _err("Attività non trovata.", status=404)
    return _ok(data)


@bridge_read_guard
@require_http_methods(["GET"])
def api_bridge_jobs(request: HttpRequest) -> JsonResponse:
    return _ok(serialize_jobs())


@bridge_read_guard
@require_http_methods(["GET"])
def api_bridge_inventario(request: HttpRequest) -> JsonResponse:
    return _ok(serialize_inventario())


@bridge_read_guard
@require_http_methods(["GET"])
def api_bridge_stabilimenti(request: HttpRequest) -> JsonResponse:
    return _ok(serialize_stabilimenti())


@bridge_read_guard
@require_http_methods(["GET"])
def api_bridge_responsabili(request: HttpRequest) -> JsonResponse:
    return _ok(serialize_responsabili())


@bridge_read_guard
@require_http_methods(["GET"])
def api_bridge_tipi_attivita(request: HttpRequest) -> JsonResponse:
    return _ok(serialize_tipi_attivita())


@bridge_read_guard
@require_http_methods(["GET"])
def api_bridge_problemi(request: HttpRequest) -> JsonResponse:
    return _ok(serialize_problemi())


@bridge_read_guard
@require_http_methods(["GET"])
def api_bridge_app_settings(request: HttpRequest) -> JsonResponse:
    return _ok(serialize_app_settings())


@bridge_read_guard
@require_http_methods(["GET"])
def api_bridge_config_testa(request: HttpRequest) -> JsonResponse:
    return _ok(serialize_config_testa())


# ── Write: attività ───────────────────────────────────────────────────────────


@bridge_write_guard
@require_http_methods(["POST"])
def api_bridge_attivita_create(request: HttpRequest) -> JsonResponse:
    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    return _forward_response(
        _delegate(
            views_attivita.api_salva_attivita,
            request,
            "POST",
            "/api/bridge/attivita/",
            data=parsed,
        )
    )


@bridge_write_guard
@require_http_methods(["PUT"])
def api_bridge_attivita_update(request: HttpRequest, pk: int) -> JsonResponse:
    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    try:
        result = update_attivita(pk, parsed)
    except BridgeWriteError as exc:
        extra = {"conflicts": exc.conflicts} if exc.conflicts is not None else {}
        return _err(exc.message, status=exc.status_code, **extra)
    return JsonResponse({"ok": True, **result})


@bridge_write_guard
@require_http_methods(["DELETE"])
def api_bridge_attivita_delete(request: HttpRequest, pk: int) -> JsonResponse:
    try:
        delete_attivita(pk)
    except BridgeWriteError as exc:
        return _err(exc.message, status=exc.status_code)
    return _ok()


# ── Write: jobs ───────────────────────────────────────────────────────────────


@bridge_write_guard
@require_http_methods(["POST"])
def api_bridge_jobs_create(request: HttpRequest) -> JsonResponse:
    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    return _forward_response(
        _delegate(
            views.api_jobs_create, request, "POST", "/api/bridge/jobs/", data=parsed
        )
    )


@bridge_write_guard
@require_http_methods(["PUT"])
def api_bridge_jobs_update(request: HttpRequest) -> JsonResponse:
    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    return _forward_response(
        _delegate(
            views.api_jobs_update, request, "PUT", "/api/bridge/jobs/", data=parsed
        )
    )


@bridge_write_guard
@require_http_methods(["DELETE"])
def api_bridge_jobs_delete(request: HttpRequest) -> JsonResponse:
    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    return _forward_response(
        _delegate(
            views.api_jobs_delete, request, "DELETE", "/api/bridge/jobs/", data=parsed
        )
    )


# ── Write: inventario ─────────────────────────────────────────────────────────


@bridge_write_guard
@require_http_methods(["POST"])
def api_bridge_inventario_create(request: HttpRequest, table: str) -> JsonResponse:
    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    return _forward_response(
        _delegate(
            views.api_inventario_add,
            request,
            "POST",
            f"/api/bridge/inventario/{table}/",
            data=parsed,
            table=table,
        )
    )


@bridge_write_guard
@require_http_methods(["PUT", "PATCH", "DELETE"])
def api_bridge_inventario_detail(
    request: HttpRequest, table: str, uid: str
) -> JsonResponse:
    if request.method == "DELETE":
        return _forward_response(
            _delegate(
                views.api_inventario_detail,
                request,
                "DELETE",
                f"/api/bridge/inventario/{table}/{uid}/",
                table=table,
                uid=uid,
            )
        )
    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    return _forward_response(
        _delegate(
            views.api_inventario_detail,
            request,
            request.method,
            f"/api/bridge/inventario/{table}/{uid}/",
            data=parsed,
            table=table,
            uid=uid,
        )
    )


# ── Write: stabilimenti / responsabili ────────────────────────────────────────


@bridge_write_guard
@require_http_methods(["POST"])
def api_bridge_stabilimenti_create(request: HttpRequest) -> JsonResponse:
    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    return _forward_response(
        _delegate(
            views.api_stabilimenti_create,
            request,
            "POST",
            "/api/bridge/stabilimenti/",
            data=parsed,
        )
    )


@bridge_write_guard
@require_http_methods(["PUT", "DELETE"])
def api_bridge_stabilimenti_detail(request: HttpRequest, pk: int) -> JsonResponse:
    if request.method == "DELETE":
        return _forward_response(
            _delegate(
                views.api_stabilimenti_detail,
                request,
                "DELETE",
                f"/api/bridge/stabilimenti/{pk}/",
                pk=pk,
            )
        )
    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    return _forward_response(
        _delegate(
            views.api_stabilimenti_detail,
            request,
            "PUT",
            f"/api/bridge/stabilimenti/{pk}/",
            data=parsed,
            pk=pk,
        )
    )


@bridge_write_guard
@require_http_methods(["POST"])
def api_bridge_responsabili_create(request: HttpRequest) -> JsonResponse:
    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    return _forward_response(
        _delegate(
            views.api_responsabili_create,
            request,
            "POST",
            "/api/bridge/responsabili/",
            data=parsed,
        )
    )


@bridge_write_guard
@require_http_methods(["PUT", "DELETE"])
def api_bridge_responsabili_detail(request: HttpRequest, pk: int) -> JsonResponse:
    if request.method == "DELETE":
        return _forward_response(
            _delegate(
                views.api_responsabili_detail,
                request,
                "DELETE",
                f"/api/bridge/responsabili/{pk}/",
                pk=pk,
            )
        )
    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    return _forward_response(
        _delegate(
            views.api_responsabili_detail,
            request,
            "PUT",
            f"/api/bridge/responsabili/{pk}/",
            data=parsed,
            pk=pk,
        )
    )


@bridge_write_guard
@require_http_methods(["POST"])
def api_bridge_tipi_attivita_create(request: HttpRequest) -> JsonResponse:
    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    return _forward_response(
        _delegate(
            views.api_tipi_attivita_create,
            request,
            "POST",
            "/api/bridge/tipi-attivita/",
            data=parsed,
        )
    )


@bridge_write_guard
@require_http_methods(["PUT", "DELETE"])
def api_bridge_tipi_attivita_detail(request: HttpRequest, pk: int) -> JsonResponse:
    if request.method == "DELETE":
        return _forward_response(
            _delegate(
                views.api_tipi_attivita_detail,
                request,
                "DELETE",
                f"/api/bridge/tipi-attivita/{pk}/",
                pk=pk,
            )
        )
    parsed = _parse_json(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    return _forward_response(
        _delegate(
            views.api_tipi_attivita_detail,
            request,
            "PUT",
            f"/api/bridge/tipi-attivita/{pk}/",
            data=parsed,
            pk=pk,
        )
    )
