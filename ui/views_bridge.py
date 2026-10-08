"""JSON bridge API for external devices (tablets).

Read endpoints answer ``{"ok": true, "data": ...}`` from the shared read
layer.  Write endpoints share the web application's own view functions,
called on the very same request: the business rules, the audit log (whose
``source`` is ``tablet`` because the path starts with ``/api/bridge/``) and
the response bodies are therefore identical on both channels.
"""

from __future__ import annotations

from functools import wraps

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ui import views, views_attivita
from ui.permissions import (
    WRITE_DENIED_MESSAGE,
    get_ruolo,
    has_write_access,
    is_admin_user,
)
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
    serialize_stabilimenti,
    serialize_tipi_attivita,
)
from ui.services.bridge_write import BridgeWriteError
from ui.services.parsing import parse_json_body
from ui.services.scan_substitution import scan_resource
from ui.views_auth import user_stabilimento_name


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


def bridge_read_guard(view):
    """Require an authenticated user for read endpoints."""

    @wraps(view)
    @login_required
    def wrapper(request: HttpRequest, *args, **kwargs):
        return view(request, *args, **kwargs)

    return wrapper


def bridge_write_guard(view):
    """Require write permission for mutating endpoints.

    Read-only accounts (``lettura`` layer) are refused with HTTP 403 so the
    tablet can show a clear "sola lettura" message.
    """

    @wraps(view)
    @login_required
    @csrf_exempt
    def wrapper(request: HttpRequest, *args, **kwargs):
        if not has_write_access(request.user):
            return _err(WRITE_DENIED_MESSAGE, status=403)
        return view(request, *args, **kwargs)

    return wrapper


# ── Auth ──────────────────────────────────────────────────────────────────────


@csrf_exempt
@require_http_methods(["POST"])
def api_bridge_auth_login(request: HttpRequest) -> JsonResponse:
    """Authenticate via JSON and start a Django session."""
    parsed = parse_json_body(request)
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
    logout(request)
    return _ok()


@require_http_methods(["GET"])
@login_required
def api_bridge_auth_me(request: HttpRequest) -> JsonResponse:
    """Return the authenticated user profile."""
    return _ok(_user_payload(request.user))


# ── Status / import ───────────────────────────────────────────────────────────


@require_http_methods(["GET"])
@login_required
def api_bridge_status(request: HttpRequest) -> JsonResponse:
    """Return the dataset status for tablet polling.

    The data live in the database, so the dataset is always ready.  The
    response keeps the fields of the former import pipeline so existing
    tablet clients keep working.
    """
    return _ok(
        {
            "status": "ready",
            "detail": "",
            "pending_queue_count": 0,
            "sync_phase": "ready",
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def api_bridge_import(request: HttpRequest) -> JsonResponse:
    """Refuse the former MS Project / Excel import with HTTP 410.

    The data live in the database, so there is nothing to import.  The route
    is kept so older tablet clients get an explicit answer.
    """
    return JsonResponse(
        {
            "ok": False,
            "accepted": False,
            "status": "ready",
            "message": (
                "Import da MS Project/Excel dismesso: i dati risiedono nel database."
            ),
        },
        status=410,
    )


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
#
# Each endpoint is the web view itself, run on the bridge request: same
# method, same JSON body, same user and session.  Nothing is re-encoded.


@bridge_write_guard
@require_http_methods(["POST"])
def api_bridge_attivita_create(request: HttpRequest) -> JsonResponse:
    return views_attivita.api_salva_attivita(request)


@bridge_write_guard
@require_http_methods(["PUT"])
def api_bridge_attivita_update(request: HttpRequest, pk: int) -> JsonResponse:
    parsed = parse_json_body(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    return views.tasks_update_response(request, pk, parsed)


@bridge_write_guard
@require_http_methods(["POST"])
def api_bridge_attivita_scan(request: HttpRequest, pk: int) -> JsonResponse:
    """Validate a scanned component and, on confirmation, apply a substitution.

    Body: ``{"tipo": "mandrino", "codice": "MND-117", "atteso": "MND-102",
    "conferma": false}``.  Heads and generators require the exact assigned
    UID; accessories may be replaced by a dimensionally compatible item that
    is free, or held only by planned activities, in the activity window.

    Without ``conferma`` nothing is written: the response says whether a
    confirmation is needed and which message to show.  Refusals carry
    ``motivo`` (``non_corrispondente``, ``non_compatibile``, ``occupata``,
    ``requisiti``, ``misura``, ``atteso``, ``date``, ``non_trovato``).
    """
    parsed = parse_json_body(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    try:
        result = scan_resource(
            pk,
            tipo=str(parsed.get("tipo") or ""),
            codice=str(parsed.get("codice") or ""),
            atteso=parsed.get("atteso"),
            conferma=bool(parsed.get("conferma", False)),
        )
    except BridgeWriteError as exc:
        extra: dict = {}
        motivo = getattr(exc, "motivo", "")
        if motivo:
            extra["motivo"] = motivo
        if exc.conflicts is not None:
            extra["conflicts"] = exc.conflicts
        return _err(exc.message, status=exc.status_code, **extra)

    if result.get("applicata"):
        from ui.models import Attivita
        from ui.services.operation_log import record_operation

        activity_id = (
            Attivita.objects.filter(pk=pk).values_list("activity_id", flat=True).first()
        )
        descrizione = (
            f"Sostituzione {result['tipo']} {result.get('atteso') or '—'} → "
            f"{result['codice']} su scansione tablet"
        )
        n_riassegnazioni = len(result.get("riassegnazioni") or [])
        if n_riassegnazioni:
            descrizione += f"; {n_riassegnazioni} attività ricoperte automaticamente"
        record_operation(
            request,
            azione="attivita_sostituzione_tablet",
            descrizione=descrizione,
            riferimento=activity_id or "",
        )

    return _ok(result)


@bridge_write_guard
@require_http_methods(["DELETE"])
def api_bridge_attivita_delete(request: HttpRequest, pk: int) -> JsonResponse:
    return views.attivita_delete_response(request, pk)


# ── Write: jobs ───────────────────────────────────────────────────────────────


@bridge_write_guard
@require_http_methods(["POST"])
def api_bridge_jobs_create(request: HttpRequest) -> JsonResponse:
    return views.api_jobs_create(request)


@bridge_write_guard
@require_http_methods(["PUT"])
def api_bridge_jobs_update(request: HttpRequest) -> JsonResponse:
    return views.api_jobs_update(request)


@bridge_write_guard
@require_http_methods(["DELETE"])
def api_bridge_jobs_delete(request: HttpRequest) -> JsonResponse:
    return views.api_jobs_delete(request)


# ── Write: inventario ─────────────────────────────────────────────────────────


@bridge_write_guard
@require_http_methods(["POST"])
def api_bridge_inventario_create(request: HttpRequest, table: str) -> JsonResponse:
    return views.api_inventario_add(request, table)


@bridge_write_guard
@require_http_methods(["PUT", "PATCH", "DELETE"])
def api_bridge_inventario_detail(
    request: HttpRequest, table: str, uid: str
) -> JsonResponse:
    return views.api_inventario_detail(request, table, uid)


# ── Write: stabilimenti / responsabili / tipi attività ────────────────────────


@bridge_write_guard
@require_http_methods(["POST"])
def api_bridge_stabilimenti_create(request: HttpRequest) -> JsonResponse:
    return views.api_stabilimenti_create(request)


@bridge_write_guard
@require_http_methods(["PUT", "DELETE"])
def api_bridge_stabilimenti_detail(request: HttpRequest, pk: int) -> JsonResponse:
    return views.api_stabilimenti_detail(request, pk)


@bridge_write_guard
@require_http_methods(["POST"])
def api_bridge_responsabili_create(request: HttpRequest) -> JsonResponse:
    return views.api_responsabili_create(request)


@bridge_write_guard
@require_http_methods(["PUT", "DELETE"])
def api_bridge_responsabili_detail(request: HttpRequest, pk: int) -> JsonResponse:
    return views.api_responsabili_detail(request, pk)


@bridge_write_guard
@require_http_methods(["POST"])
def api_bridge_tipi_attivita_create(request: HttpRequest) -> JsonResponse:
    return views.api_tipi_attivita_create(request)


@bridge_write_guard
@require_http_methods(["PUT", "DELETE"])
def api_bridge_tipi_attivita_detail(request: HttpRequest, pk: int) -> JsonResponse:
    return views.api_tipi_attivita_detail(request, pk)
