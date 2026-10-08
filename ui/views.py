"""Views for the ui application."""

import json
import logging
from datetime import date

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ui.permissions import deny_if_read_only, has_write_access, write_required
from ui.services.bridge_write import (
    BridgeWriteError,
    delete_attivita,
    update_attivita,
)
from ui.services.identifiers import parse_job_id
from ui.services.planning_read import (
    fetch_gantt,
    fetch_grouped,
    job_summary,
    load_task_dict,
)
from ui.services.runtime_config import load_runtime_config
from ui.services.inventory import (
    delete_resource,
    insert_resource,
    patch_resource,
    update_resource,
)
from ui.models import (
    Attivita,
    ConfigurazioneTesta,
    GeneratoreCompatibile,
    GeneratoreItem,
    Job,
    MandrinoItem,
    MascheraItem,
    Responsabile,
    Stabilimento,
    TastatoreItem,
    TestaItem,
    TipoAttivita,
)

logger = logging.getLogger(__name__)

_INVENTORY_TABLES = {"Generatore", "Mandrino", "Maschera", "Tastatore", "Testa"}


def _parse_job_money(value) -> float | None:
    """Parse an optional monetary amount (e.g. job ``total_cost``).

    Accepts plain numbers and Italian formatting such as ``200.000`` or
    ``1.234,56``.
    """
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        amount = float(value)
        if amount < 0:
            raise ValueError("negative")
        return amount
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") == 1:
        left, right = text.split(".", 1)
        if len(right) == 3 and right.isdigit():
            text = left + right
    else:
        text = text.replace(".", "")
    try:
        amount = float(text)
    except ValueError as exc:
        raise ValueError("invalid") from exc
    if amount < 0:
        raise ValueError("negative")
    return amount


def _parse_job_penalty(value) -> float | None:
    """Parse an optional penalty percentage."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        penalty = float(value)
    else:
        text = str(value).strip().replace(",", ".")
        try:
            penalty = float(text)
        except ValueError as exc:
            raise ValueError("invalid") from exc
    if penalty < 0:
        raise ValueError("negative")
    return penalty


@login_required
def index(request: HttpRequest):
    """Render the main application page.

    The page receives the user's own plant, which pre-selects the plant
    filter so each user starts on the activities of their stabilimento.

    Args:
        request: The incoming HTTP request.

    Returns:
        HttpResponse: The rendered index template.
    """
    profile = getattr(request.user, "profile", None)
    user_plant = profile.stabilimento.nome if profile and profile.stabilimento else ""
    return render(
        request,
        "ui/index.html",
        {"home_config": {"userPlant": user_plant}},
    )


@login_required
def pianificazione(request: HttpRequest):
    """Render the planning page with the grouped commessa+activity layout.

    Activities are grouped by commessa so the template can render the
    card-left / activity-rows-right layout.  Wizard dropdowns are
    populated from ORM-managed and imported sources.

    Args:
        request: The incoming HTTP request.

    Returns:
        HttpResponse: The rendered pianificazione template.
    """
    from ui.services.activity_inventory import (
        get_responsabili,
        get_stabilimenti,
        get_testa_models,
        get_tipi_attivita,
    )
    from ui.services.reconcile import ensure_daily_sync

    # Reading never reconciles: the write paths do.  Only the calendar-driven
    # «ritardo» notes are synced here, once a day.
    ensure_daily_sync()
    groups = fetch_grouped()

    stab_objs = list(Stabilimento.objects.values("nome", "colore"))
    stab_orm = [s["nome"] for s in stab_objs]
    resp_orm = list(Responsabile.objects.values_list("nome", flat=True))
    tipo_orm = list(TipoAttivita.objects.values_list("nome", flat=True))
    stabilimenti = sorted(set(stab_orm + get_stabilimenti()))
    responsabili = sorted(set(resp_orm + get_responsabili()))
    tipi_attivita = sorted(set(tipo_orm + get_tipi_attivita()))
    plant_colors = {s["nome"]: s["colore"] for s in stab_objs}

    return render(
        request,
        "ui/pianificazione.html",
        {
            "groups": groups,
            "stabilimenti": stabilimenti,
            "stabilimenti_json": json.dumps(stabilimenti),
            "plant_colors_json": json.dumps(plant_colors),
            "responsabili": responsabili,
            "responsabili_json": json.dumps(responsabili),
            "penale_max_json": json.dumps(load_runtime_config().penale_max),
            "testa_models_json": json.dumps(get_testa_models()),
            "tipi_attivita": tipi_attivita,
            "tipi_attivita_json": json.dumps(tipi_attivita),
        },
    )


@login_required
def inventario(request: HttpRequest):
    """Render the inventory page with one table per equipment category.

    Fetches all rows from each inventory table (Generatore, Mandrino,
    Maschera, Tastatore, Testa) and passes them to the template as
    separate context keys.  The active table is toggled client-side.

    Each row is annotated with its availability today (see
    :func:`ui.services.resource_availability.annotate_inventory_rows`); the
    same descriptors are handed to the page as JSON so rows added or edited
    without a reload keep the column filled.

    Args:
        request: The incoming HTTP request.

    Returns:
        HttpResponse: The rendered inventario template.
    """
    from ui.services.resource_availability import annotate_inventory_rows

    tables = {
        "Generatore": "generatori",
        "Mandrino": "mandrini",
        "Maschera": "maschere",
        "Tastatore": "tastatori",
        "Testa": "teste",
    }
    today = date.today()
    context = {
        ctx_key: annotate_inventory_rows(tbl, _fetch_inventory_table(tbl), today)
        for tbl, ctx_key in tables.items()
    }
    context["disponibilita_json"] = json.dumps(
        {
            tbl: {row["uid"]: row["disponibilita"] for row in context[ctx_key]}
            for tbl, ctx_key in tables.items()
        }
    )
    stab_objs = list(Stabilimento.objects.order_by("nome").values("nome", "colore"))
    context["stabilimenti"] = [s["nome"] for s in stab_objs]
    context["stabilimenti_json"] = json.dumps(context["stabilimenti"])
    context["plant_colors_json"] = json.dumps(
        {s["nome"]: s["colore"] for s in stab_objs}
    )
    return render(request, "ui/inventario.html", context)


def _fetch_inventory_table(table_name: str) -> list[dict]:
    """Return all rows from a named inventory table as a list of dicts.

    Args:
        table_name (str): The inventory category name.  Must be one of:
            Generatore, Mandrino, Maschera, Tastatore, Testa.

    Returns:
        list[dict]: One dict per row, keyed by field name.
            Returns an empty list if the table is empty or the name is invalid.
    """
    _models = {
        "Generatore": GeneratoreItem,
        "Mandrino": MandrinoItem,
        "Maschera": MascheraItem,
        "Tastatore": TastatoreItem,
        "Testa": TestaItem,
    }
    model_class = _models.get(table_name)
    if model_class is None:
        return []
    return list(model_class.objects.order_by("uid").values())


@write_required
@csrf_exempt
@require_http_methods(["POST"])
def api_inventario_add(request: HttpRequest, table: str) -> JsonResponse:
    """Insert a new resource into the given inventory table.

    Accepts a JSON body whose keys correspond to the columns of the target
    table.  The ``uid`` field is required; all other fields are optional and
    default to ``null`` when omitted.

    Response schema (success)::

        {"ok": true, "uid": "<uid>"}

    Response schema (error)::

        {"ok": false, "error": "<message>"}

    Args:
        request: The incoming HTTP POST request with a JSON body.
        table: The inventory table name (e.g. ``Generatore``).

    Returns:
        JsonResponse: 201 on success; 400 on validation error; 409 on
            duplicate uid; 503 when the database is not yet available.
    """
    if table not in _INVENTORY_TABLES:
        return JsonResponse(
            {"ok": False, "error": f"Tabella non valida: {table}"}, status=400
        )

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    uid = (data.get("uid") or "").strip()
    if not uid:
        return JsonResponse(
            {"ok": False, "error": "Il campo 'uid' è obbligatorio."}, status=400
        )

    try:
        insert_resource(table, data)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=409)
    except RuntimeError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=503)

    from ui.services.operation_log import record_operation

    record_operation(
        request,
        azione="inventario_crea",
        descrizione=f"Creata risorsa {table} {uid}",
        riferimento=f"{table}:{uid}",
    )

    return JsonResponse({"ok": True, "uid": uid}, status=201)


@write_required
@csrf_exempt
@require_http_methods(["PUT", "PATCH", "DELETE"])
def api_inventario_detail(request: HttpRequest, table: str, uid: str) -> JsonResponse:
    """Update (PUT/PATCH) or delete (DELETE) a resource by its uid.

    PUT accepts a JSON body whose keys correspond to the columns of the target
    table.  The ``uid`` field is required; it may differ from the URL ``uid``
    parameter to support renaming the primary key.  Omitted fields are stored
    as ``null``.

    PATCH accepts a JSON body with only the fields to change.  Omitted fields
    keep their current values.  ``uid`` is optional and can be used to rename
    the resource.

    Response schema (success)::

        {"ok": true, "uid": "<new_uid>", "disponibilita": {...}}  # PUT / PATCH
        {"ok": true}                                               # DELETE

    ``disponibilita`` is the item's availability today (see
    :func:`ui.services.resource_availability.resource_availability`), so the
    page can repaint the dot after a maintenance change.

    Response schema (error)::

        {"ok": false, "error": "<message>"}

    Args:
        request: The incoming HTTP request.
        table: The inventory table name (e.g. ``Generatore``).
        uid: The current primary-key value of the row.

    Returns:
        JsonResponse: 200 on success; 400 on invalid table/body; 404 when the
            uid does not exist; 409 on duplicate uid; 503 when the database is
            not yet available.
    """
    if table not in _INVENTORY_TABLES:
        return JsonResponse(
            {"ok": False, "error": f"Tabella non valida: {table}"}, status=400
        )

    if request.method == "DELETE":
        try:
            delete_resource(table, uid)
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=404)
        except RuntimeError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=503)

        from ui.services.operation_log import record_operation

        record_operation(
            request,
            azione="inventario_elimina",
            descrizione=f"Eliminata risorsa {table} {uid}",
            riferimento=f"{table}:{uid}",
        )

        return JsonResponse({"ok": True})

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    if not isinstance(data, dict):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    if request.method == "PATCH":
        try:
            new_uid = patch_resource(table, uid, data)
        except ValueError as exc:
            msg = str(exc)
            if "già presente" in msg:
                status_code = 409
            elif "non trovato" in msg:
                status_code = 404
            else:
                status_code = 400
            return JsonResponse({"ok": False, "error": msg}, status=status_code)
        except RuntimeError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=503)

        from ui.services.operation_log import record_operation

        record_operation(
            request,
            azione="inventario_modifica",
            descrizione=f"Aggiornata risorsa {table} {uid} → {new_uid}",
            riferimento=f"{table}:{new_uid}",
        )

        return _edited_resource_response(table, new_uid)

    # PUT
    new_uid = (data.get("uid") or "").strip()
    if not new_uid:
        return JsonResponse(
            {"ok": False, "error": "Il campo 'uid' è obbligatorio."}, status=400
        )

    try:
        update_resource(table, uid, data)
    except ValueError as exc:
        status_code = 409 if "già presente" in str(exc) else 404
        return JsonResponse({"ok": False, "error": str(exc)}, status=status_code)
    except RuntimeError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=503)

    from ui.services.operation_log import record_operation

    record_operation(
        request,
        azione="inventario_modifica",
        descrizione=f"Aggiornata risorsa {table} {uid} → {new_uid}",
        riferimento=f"{table}:{new_uid}",
    )

    return _edited_resource_response(table, new_uid)


def _edited_resource_response(table: str, uid: str) -> JsonResponse:
    """Return the success payload of an inventory edit, with today's availability.

    Entering maintenance turns the availability dot red (and may move the
    unit's bookings to a spare), so the page repaints it from ``disponibilita``.
    """
    from ui.services.resource_availability import resource_availability

    return JsonResponse(
        {"ok": True, "uid": uid, "disponibilita": resource_availability(table, uid)}
    )


@write_required
@require_http_methods(["PUT"])
def api_tasks_update(request: HttpRequest) -> JsonResponse:
    """Update a single Attivita record.

    Looks up the :class:`~ui.models.Attivita` record by ``orm_id`` and
    applies any supplied subset of updatable fields.

    Args:
        request: The incoming HTTP PUT request with a JSON body containing
            ``orm_id`` and any combination of ``start``, ``finish``,
            ``actual_start``, ``actual_finish``, ``plant``, ``responsible``.

    Returns:
        JsonResponse: 200 ``{"ok": true}`` on success; 400/404/500 on error.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    orm_id = data.get("orm_id")
    if not orm_id:
        return JsonResponse({"ok": False, "error": "orm_id mancante."}, status=400)
    return tasks_update_response(request, int(orm_id), data)


def tasks_update_response(
    request: HttpRequest, orm_id: int, data: dict
) -> JsonResponse:
    """Apply an activity update and build the response both channels return.

    Shared by ``/api/tasks/update/`` (``orm_id`` in the body) and the tablet
    bridge (``pk`` in the URL): same rules, same audit entry, same payload
    with the refreshed ``task`` row and the ``job`` header.
    """
    try:
        result = update_attivita(orm_id, data)
    except BridgeWriteError as exc:
        payload = {"ok": False, "error": exc.message}
        if exc.conflicts is not None:
            payload["conflicts"] = exc.conflicts
        if exc.missing_resources is not None:
            payload["missing_resources"] = exc.missing_resources
        return JsonResponse(payload, status=exc.status_code)
    except Exception as exc:
        logger.error("Errore aggiornamento Attivita pk=%s: %s", orm_id, exc)
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    from ui.services.operation_log import describe_attivita_update, record_operation

    record_operation(
        request,
        azione="attivita_modifica",
        descrizione=(
            f"Attività {result['activity_id']}: {describe_attivita_update(data)}"
        ),
        riferimento=result["activity_id"],
    )

    task = load_task_dict(orm_id)
    if task is not None:
        result["task"] = task
        result["warning_count"] = task["warning_count"]
        result["note_count"] = task["note_count"]
        result["job"] = job_summary(task["commessa"])

    return JsonResponse({"ok": True, **result})


@write_required
@csrf_exempt
@require_http_methods(["POST"])
def api_jobs_create(request: HttpRequest) -> JsonResponse:
    """Create a new commessa (Job) record.

    Validates that the commessa name is non-empty, that the commessa does not
    already exist, and that penalty does not exceed the configured maximum.

    Args:
        request: POST request with a JSON body containing:
            ``commessa`` (str, required), ``delivery_date`` (str|null),
            ``total_cost`` (float|null), ``penalty`` (float|null).

    Returns:
        JsonResponse: 201 ``{"ok": true, "job": {...}}`` on success;
            400 for validation errors; 409 if the job already exists;
            500 on unexpected errors.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    commessa = (data.get("commessa") or "").strip()
    if not commessa:
        return JsonResponse(
            {"ok": False, "error": "Il campo Commessa è obbligatorio."}, status=400
        )

    if Job.objects.filter(commessa=commessa).exists():
        return JsonResponse(
            {
                "ok": False,
                "error": f"La commessa «{commessa}» è già presente in archivio.",
            },
            status=409,
        )

    try:
        total_cost = _parse_job_money(data.get("total_cost"))
    except ValueError:
        return JsonResponse(
            {"ok": False, "error": "Costo totale non valido."}, status=400
        )

    try:
        penalty_val = _parse_job_penalty(data.get("penalty"))
    except ValueError:
        return JsonResponse(
            {"ok": False, "error": "Valore penale non valido."}, status=400
        )
    if penalty_val is not None:
        penale_max = load_runtime_config().penale_max
        if penalty_val > penale_max:
            return JsonResponse(
                {"ok": False, "error": f"La penale non può superare il {penale_max}%."},
                status=400,
            )

    try:
        job = Job.objects.create(
            commessa=commessa,
            total_cost=total_cost,
            penalty=penalty_val,
            delivery_date=data.get("delivery_date") or None,
        )
    except Exception as exc:
        logger.error("Errore creazione Job: %s", exc)
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    from ui.services.operation_log import record_operation

    record_operation(
        request,
        azione="commessa_crea",
        descrizione=f"Creata commessa {job.commessa}",
        riferimento=job.commessa,
    )

    return JsonResponse(
        {
            "ok": True,
            "job": {
                "job_id": job.job_id,
                "commessa": job.commessa,
                "total_cost": job.total_cost,
                "penalty": job.penalty,
                "delivery_date": job.delivery_date,
                "n_activities": 0,
                "n_items": 0,
            },
        },
        status=201,
    )


@write_required
@csrf_exempt
@require_http_methods(["PUT"])
def api_jobs_update(request: HttpRequest) -> JsonResponse:
    """Update a single job row in the ``Jobs`` table.

    The job is identified by ``commessa`` in the JSON body; the ``commessa``
    field itself cannot be changed through this endpoint.

    Args:
        request: The incoming HTTP PUT request with a JSON body containing
            ``commessa`` and the updatable fields ``total_cost``, ``penalty``,
            ``delivery_date``.

    Returns:
        JsonResponse: 200 ``{"ok": true}`` on success; 400/404/503 on error.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    commessa = (data.get("commessa") or "").strip()
    job_id = (data.get("job_id") or "").strip()
    if not commessa and not job_id:
        return JsonResponse(
            {"ok": False, "error": "commessa (o job_id) è obbligatoria."},
            status=400,
        )

    try:
        total_cost = _parse_job_money(data.get("total_cost"))
    except ValueError:
        return JsonResponse(
            {"ok": False, "error": "Costo totale non valido."}, status=400
        )

    try:
        penalty_val = _parse_job_penalty(data.get("penalty"))
    except ValueError:
        return JsonResponse(
            {"ok": False, "error": "Valore penale non valido."}, status=400
        )
    if penalty_val is not None:
        penale_max = load_runtime_config().penale_max
        if penalty_val > penale_max:
            return JsonResponse(
                {
                    "ok": False,
                    "error": f"La penale non può superare il {penale_max}%.",
                },
                status=400,
            )

    delivery_date = data.get("delivery_date") or None

    try:
        qs = (
            Job.objects.filter(commessa=commessa)
            if commessa
            else Job.objects.filter(job_id=job_id)
        )
        updated = qs.update(
            total_cost=total_cost,
            penalty=penalty_val,
            delivery_date=delivery_date,
        )
        if updated == 0:
            if not commessa:
                return JsonResponse(
                    {"ok": False, "error": "Commessa non trovata."}, status=404
                )
            Job.objects.create(
                commessa=commessa,
                total_cost=total_cost,
                penalty=penalty_val,
                delivery_date=delivery_date,
            )
    except Exception as exc:
        logger.error("Errore aggiornamento Job: %s", exc)
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    from ui.services.operation_log import record_operation

    record_operation(
        request,
        azione="commessa_modifica",
        descrizione=f"Aggiornata commessa {commessa or job_id}",
        riferimento=commessa or job_id,
    )

    return JsonResponse(
        {"ok": True, "job": job_summary(commessa) if commessa else None}
    )


@login_required
def api_jobs_check(request: HttpRequest) -> JsonResponse:
    """Check whether a job (commessa) already exists and list predecessor candidates.

    Used by the add-activity wizard after WPS analysis to determine
    whether the user needs to create a new commessa or can proceed.
    Predecessor candidates are all activities of the same ``commessa``.

    Args:
        request: GET request with ``commessa`` query parameter.

    Returns:
        JsonResponse: ``{"ok": True, "exists": bool, "predecessors": [...]}``
        where ``exists`` is ``True`` when a :class:`~ui.models.Job` with the
        given ``commessa`` value is already present.
    """
    commessa = (request.GET.get("commessa") or "").strip()
    job_id = (request.GET.get("job_id") or "").strip()
    if not commessa and not job_id:
        return JsonResponse(
            {"ok": False, "error": "commessa (o job_id) è obbligatoria."}, status=400
        )
    exists = (
        Job.objects.filter(commessa=commessa).exists()
        if commessa
        else Job.objects.filter(job_id=job_id).exists()
    )
    # Any activity of the same commessa can be chosen as predecessor.
    predecessor_qs = Attivita.objects.filter(commessa=commessa).order_by(
        "item", F("data_inizio").asc(nulls_first=True), "id"
    )
    predecessors = [
        {
            "id": a.pk,
            "activity_id": a.activity_id,
            "activity": a.activity,
            "item": a.item,
            "start": a.data_inizio.isoformat() if a.data_inizio else None,
            "finish": a.data_fine.isoformat() if a.data_fine else None,
        }
        for a in predecessor_qs
    ]
    return JsonResponse({"ok": True, "exists": exists, "predecessors": predecessors})


@login_required
@csrf_exempt
@require_http_methods(["GET", "PUT"])
def api_app_settings(request: HttpRequest) -> JsonResponse:
    """Read application settings from environment variables.

    PUT is intentionally disabled: settings are now managed via `.env`.
    """
    runtime = load_runtime_config()
    if request.method == "PUT":
        return JsonResponse(
            {
                "ok": False,
                "error": "Parametri configurabili solo da file .env. Riavvia il server dopo le modifiche.",
            },
            status=400,
        )
    return JsonResponse(
        {
            "ok": True,
            "penale_max": runtime.penale_max,
            "solver_time_limit": runtime.solver_time_limit,
            "openai_wps_model": runtime.openai_wps_model,
        }
    )


@write_required
@require_http_methods(["DELETE"])
def api_attivita_delete(request: HttpRequest) -> JsonResponse:
    """Delete a single Attivita record together with its resources.

    Args:
        request: The incoming HTTP DELETE request with a JSON body containing
            ``orm_id``.

    Returns:
        JsonResponse: 200 ``{"ok": true}`` on success; 400/404/500 on error.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    orm_id = data.get("orm_id")
    if not orm_id:
        return JsonResponse({"ok": False, "error": "orm_id mancante."}, status=400)
    return attivita_delete_response(request, int(orm_id))


def attivita_delete_response(request: HttpRequest, orm_id: int) -> JsonResponse:
    """Delete an activity and build the response both channels return."""
    activity_id = (
        Attivita.objects.filter(pk=orm_id).values_list("activity_id", flat=True).first()
    )

    try:
        delete_attivita(orm_id)
    except BridgeWriteError as exc:
        return JsonResponse({"ok": False, "error": exc.message}, status=exc.status_code)
    except Exception as exc:
        logger.error("Errore eliminazione Attivita pk=%s: %s", orm_id, exc)
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    from ui.services.operation_log import record_operation

    record_operation(
        request,
        azione="attivita_elimina",
        descrizione=f"Eliminata attività {activity_id or orm_id}",
        riferimento=activity_id or "",
    )

    return JsonResponse({"ok": True})


@write_required
@require_http_methods(["DELETE"])
def api_jobs_delete(request: HttpRequest) -> JsonResponse:
    """Delete a job and all its associated activities.

    The Job row and all its :class:`~ui.models.Attivita` rows are deleted
    atomically.

    Args:
        request: The incoming HTTP DELETE request with a JSON body containing
            ``commessa``.

    Returns:
        JsonResponse: 200 ``{"ok": true}`` on success; 400/404/500 on error.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    commessa = (data.get("commessa") or "").strip()
    job_id = (data.get("job_id") or "").strip()
    if not commessa and not job_id:
        return JsonResponse(
            {"ok": False, "error": "commessa (o job_id) è obbligatoria."},
            status=400,
        )

    try:
        from ui.models import Attivita

        with transaction.atomic():
            job_qs = (
                Job.objects.filter(commessa=commessa)
                if commessa
                else Job.objects.filter(job_id=job_id)
            )
            att_qs = (
                Attivita.objects.filter(commessa=commessa)
                if commessa
                else Attivita.objects.filter(job_id=job_id)
            )
            if not job_qs.exists() and not att_qs.exists():
                return JsonResponse(
                    {"ok": False, "error": "Job non trovato."}, status=404
                )

            att_qs.delete()
            if job_qs.exists():
                job_qs.delete()
    except Exception as exc:
        logger.error(
            "Errore eliminazione Job commessa=%s job_id=%s: %s", commessa, job_id, exc
        )
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    from ui.services.reconcile import reconcile_after_delete

    reconcile_after_delete()

    from ui.services.operation_log import record_operation

    record_operation(
        request,
        azione="commessa_elimina",
        descrizione=f"Eliminata commessa {commessa or job_id}",
        riferimento=commessa or job_id,
    )

    return JsonResponse({"ok": True})


@login_required
def api_gantt(request: HttpRequest) -> JsonResponse:
    """Return every task with its scheduling data for the Gantt and the kanban.

    Response shape::

        {
          "tasks": [planning row dicts + plant_color],
          "deadlines": {"job_id": "YYYY-MM-DD"},
          "jobs": {"job_id": {commessa, status, status_label, completed,
                              n_open_problems}}
        }

    Rows carry ``status_pills`` / ``state_key`` / ``delay_days`` and
    ``moving_resources`` already computed, so the clients only display them.
    """
    from ui.services.reconcile import ensure_daily_sync

    ensure_daily_sync()
    return JsonResponse(fetch_gantt())


@login_required
def api_commesse_deadline(request: HttpRequest) -> JsonResponse:
    """GET: return all commessa deadlines. POST: set or clear one deadline.

    POST body (JSON)::

        {"job_id": "25033", "deadline": "2026-12-31"}   # set
        {"job_id": "25033", "deadline": null}            # clear

    Args:
        request: The incoming request.
    """
    if request.method == "POST":
        denied = deny_if_read_only(request)
        if denied is not None:
            return denied
        return _handle_deadline_update(request)
    rows = Job.objects.values("job_id", "delivery_date")
    return JsonResponse({r["job_id"]: r["delivery_date"] for r in rows})


def _handle_deadline_update(request: HttpRequest) -> JsonResponse:
    """Parse the POST body and update a single deadline via the Job ORM model.

    Args:
        request: A POST request with a JSON body.

    Returns:
        ``{"ok": True}`` on success, or ``{"ok": False, "error": ...}`` on failure.
    """
    try:
        data = json.loads(request.body)
        job_id = (data.get("job_id") or "").strip()
        deadline = data.get("deadline")
        if job_id:
            commessa = parse_job_id(job_id)
            if deadline:
                Job.objects.update_or_create(
                    commessa=commessa,
                    defaults={"delivery_date": deadline, "job_id": job_id},
                )
            else:
                Job.objects.filter(job_id=job_id).update(delivery_date=None)
        return JsonResponse({"ok": True})
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)


# ---------------------------------------------------------------------------
# Impostazioni — Stabilimenti & Responsabili
# ---------------------------------------------------------------------------


@login_required
def impostazioni(request: HttpRequest):
    """Render the settings page with lists of stabilimenti, responsabili, and testa configs.

    Passes JSON arrays of all available TestaItem and GeneratoreItem model strings
    to the template so the client-side JS can populate selects and chip lists
    without extra round-trips.

    Args:
        request: The incoming HTTP request.

    Returns:
        HttpResponse: The rendered impostazioni template.
    """
    all_gen_models = sorted(
        set(
            GeneratoreItem.objects.exclude(model__isnull=True)
            .exclude(model="")
            .values_list("model", flat=True)
        )
    )
    from ui.services.activity_inventory import get_testa_models

    all_testa_models = get_testa_models()
    cfgs = list(
        ConfigurazioneTesta.objects.prefetch_related("generatori_compatibili").all()
    )
    configurazioni_teste = []
    for cfg in cfgs:
        aggiunti = list(cfg.generatori_compatibili.values("id", "modello_generatore"))
        aggiunti_models = {g["modello_generatore"] for g in aggiunti}
        disponibili = [m for m in all_gen_models if m not in aggiunti_models]
        configurazioni_teste.append(
            {
                "cfg": cfg,
                "aggiunti": aggiunti,
                "disponibili": disponibili,
            }
        )

    log_entries: list[dict] = []
    log_total_count = 0
    log_next_before_id = None
    if has_write_access(request.user):
        from ui.models import OperationLog

        log_total_count = OperationLog.objects.count()
        log_rows = list(OperationLog.objects.all()[:50])
        log_entries = [_serialize_log_entry(e) for e in log_rows]
        if len(log_rows) == 50:
            log_next_before_id = log_rows[-1].pk

    return render(
        request,
        "ui/impostazioni.html",
        {
            "stabilimenti": list(Stabilimento.objects.values("id", "nome", "colore")),
            "responsabili": list(Responsabile.objects.values("id", "nome")),
            "tipi_attivita": list(TipoAttivita.objects.values("id", "nome")),
            "configurazioni_teste": configurazioni_teste,
            "all_testa_models_json": json.dumps(all_testa_models),
            "all_gen_models_json": json.dumps(all_gen_models),
            "log_entries": log_entries,
            "log_total_count": log_total_count,
            "log_next_before_id": log_next_before_id,
        },
    )


def _serialize_log_entry(entry) -> dict:
    """Serialize one OperationLog row for the JSON log endpoint/template.

    Args:
        entry: An :class:`~ui.models.OperationLog` instance.

    Returns:
        dict: Display-ready fields for the Impostazioni Log table.
    """
    return {
        "id": entry.pk,
        "timestamp_display": entry.timestamp.strftime("%d/%m/%Y %H:%M"),
        "source": entry.source,
        "source_label": dict(entry.SOURCE_CHOICES).get(entry.source, entry.source),
        "username": entry.username or "Sistema",
        "descrizione": entry.descrizione,
        "riferimento": entry.riferimento,
    }


@write_required
@require_http_methods(["GET"])
def api_operation_log(request: HttpRequest) -> JsonResponse:
    """Return a page of operation-log entries, newest first.

    Args:
        request: GET request with optional ``before_id`` (return entries
            strictly older than this pk) and ``limit`` (default 50, capped
            at 200).

    Returns:
        JsonResponse: ``{"ok": true, "items": [...], "next_before_id": int|None}``.
    """
    from ui.models import OperationLog

    try:
        limit = min(int(request.GET.get("limit", 50)), 200)
    except ValueError:
        limit = 50
    before_id = request.GET.get("before_id")

    qs = OperationLog.objects.all()
    if before_id:
        try:
            qs = qs.filter(pk__lt=int(before_id))
        except ValueError:
            pass

    rows = list(qs[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [_serialize_log_entry(r) for r in rows]
    next_before_id = rows[-1].pk if (has_more and rows) else None
    return JsonResponse({"ok": True, "items": items, "next_before_id": next_before_id})


@write_required
@csrf_exempt
@require_http_methods(["POST"])
def api_stabilimenti_create(request: HttpRequest) -> JsonResponse:
    """Create a new stabilimento.

    Accepts JSON body with ``nome`` (required) and ``colore`` (optional, defaults
    to ``#6366f1``).

    Returns:
        JsonResponse: 201 with ``{"ok": true, "id": <id>, "nome": "...", "colore": "..."}``
            on success; 400 on validation error; 409 on duplicate nome.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    nome = (data.get("nome") or "").strip()
    if not nome:
        return JsonResponse(
            {"ok": False, "error": "Il campo 'nome' è obbligatorio."}, status=400
        )

    colore = (data.get("colore") or "#6366f1").strip()

    if Stabilimento.objects.filter(nome__iexact=nome).exists():
        return JsonResponse(
            {"ok": False, "error": f"Stabilimento '{nome}' già esistente."}, status=409
        )

    obj = Stabilimento.objects.create(nome=nome, colore=colore)

    from ui.services.operation_log import record_operation

    record_operation(
        request,
        azione="anagrafica_crea",
        descrizione=f"Creato stabilimento {obj.nome}",
        riferimento=f"stabilimento:{obj.pk}",
    )

    return JsonResponse(
        {"ok": True, "id": obj.pk, "nome": obj.nome, "colore": obj.colore}, status=201
    )


@write_required
@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def api_stabilimenti_detail(request: HttpRequest, pk: int) -> JsonResponse:
    """Update (PUT) or delete (DELETE) a stabilimento by primary key.

    PUT accepts JSON body with ``nome`` (required) and ``colore`` (optional).
    DELETE requires no body.

    Returns:
        JsonResponse: 200 with updated object on PUT success; 200 ``{"ok": true}``
            on DELETE success; 400/404/409 on error.
    """
    try:
        obj = Stabilimento.objects.get(pk=pk)
    except Stabilimento.DoesNotExist:
        return JsonResponse(
            {"ok": False, "error": "Stabilimento non trovato."}, status=404
        )

    from ui.services.operation_log import record_operation

    if request.method == "DELETE":
        nome = obj.nome
        obj.delete()
        record_operation(
            request,
            azione="anagrafica_elimina",
            descrizione=f"Eliminato stabilimento {nome}",
            riferimento=f"stabilimento:{pk}",
        )
        return JsonResponse({"ok": True})

    # PUT
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    nome = (data.get("nome") or "").strip()
    if not nome:
        return JsonResponse(
            {"ok": False, "error": "Il campo 'nome' è obbligatorio."}, status=400
        )

    colore = (data.get("colore") or obj.colore).strip()

    if Stabilimento.objects.filter(nome__iexact=nome).exclude(pk=pk).exists():
        return JsonResponse(
            {"ok": False, "error": f"Stabilimento '{nome}' già esistente."}, status=409
        )

    obj.nome = nome
    obj.colore = colore
    obj.save()
    record_operation(
        request,
        azione="anagrafica_modifica",
        descrizione=f"Aggiornato stabilimento {obj.nome}",
        riferimento=f"stabilimento:{obj.pk}",
    )
    return JsonResponse(
        {"ok": True, "id": obj.pk, "nome": obj.nome, "colore": obj.colore}
    )


@write_required
@csrf_exempt
@require_http_methods(["POST"])
def api_responsabili_create(request: HttpRequest) -> JsonResponse:
    """Create a new responsabile.

    Accepts JSON body with ``nome`` (required).

    Returns:
        JsonResponse: 201 with ``{"ok": true, "id": <id>, "nome": "..."}``
            on success; 400 on validation error; 409 on duplicate nome.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    nome = (data.get("nome") or "").strip()
    if not nome:
        return JsonResponse(
            {"ok": False, "error": "Il campo 'nome' è obbligatorio."}, status=400
        )

    if Responsabile.objects.filter(nome__iexact=nome).exists():
        return JsonResponse(
            {"ok": False, "error": f"Responsabile '{nome}' già esistente."}, status=409
        )

    obj = Responsabile.objects.create(nome=nome)

    from ui.services.operation_log import record_operation

    record_operation(
        request,
        azione="anagrafica_crea",
        descrizione=f"Creato responsabile {obj.nome}",
        riferimento=f"responsabile:{obj.pk}",
    )

    return JsonResponse({"ok": True, "id": obj.pk, "nome": obj.nome}, status=201)


@write_required
@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def api_responsabili_detail(request: HttpRequest, pk: int) -> JsonResponse:
    """Update (PUT) or delete (DELETE) a responsabile by primary key.

    PUT accepts JSON body with ``nome`` (required).
    DELETE requires no body.

    Returns:
        JsonResponse: 200 with updated object on PUT success; 200 ``{"ok": true}``
            on DELETE success; 400/404/409 on error.
    """
    try:
        obj = Responsabile.objects.get(pk=pk)
    except Responsabile.DoesNotExist:
        return JsonResponse(
            {"ok": False, "error": "Responsabile non trovato."}, status=404
        )

    from ui.services.operation_log import record_operation

    if request.method == "DELETE":
        nome = obj.nome
        obj.delete()
        record_operation(
            request,
            azione="anagrafica_elimina",
            descrizione=f"Eliminato responsabile {nome}",
            riferimento=f"responsabile:{pk}",
        )
        return JsonResponse({"ok": True})

    # PUT
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    nome = (data.get("nome") or "").strip()
    if not nome:
        return JsonResponse(
            {"ok": False, "error": "Il campo 'nome' è obbligatorio."}, status=400
        )

    if Responsabile.objects.filter(nome__iexact=nome).exclude(pk=pk).exists():
        return JsonResponse(
            {"ok": False, "error": f"Responsabile '{nome}' già esistente."}, status=409
        )

    obj.nome = nome
    obj.save()
    record_operation(
        request,
        azione="anagrafica_modifica",
        descrizione=f"Aggiornato responsabile {obj.nome}",
        riferimento=f"responsabile:{obj.pk}",
    )
    return JsonResponse({"ok": True, "id": obj.pk, "nome": obj.nome})


@write_required
@csrf_exempt
@require_http_methods(["POST"])
def api_tipi_attivita_create(request: HttpRequest) -> JsonResponse:
    """Create a new activity type entry.

    Accepts JSON body with ``nome`` (required).

    Returns:
        JsonResponse: 201 with ``{"ok": true, "id": <id>, "nome": "..."}``
            on success; 400 on validation error; 409 on duplicate nome.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    nome = (data.get("nome") or "").strip()
    if not nome:
        return JsonResponse(
            {"ok": False, "error": "Il campo 'nome' è obbligatorio."}, status=400
        )

    if TipoAttivita.objects.filter(nome__iexact=nome).exists():
        return JsonResponse(
            {"ok": False, "error": f"Tipo attività '{nome}' già esistente."}, status=409
        )

    obj = TipoAttivita.objects.create(nome=nome)

    from ui.services.operation_log import record_operation

    record_operation(
        request,
        azione="anagrafica_crea",
        descrizione=f"Creato tipo attività {obj.nome}",
        riferimento=f"tipo_attivita:{obj.pk}",
    )

    return JsonResponse({"ok": True, "id": obj.pk, "nome": obj.nome}, status=201)


@write_required
@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def api_tipi_attivita_detail(request: HttpRequest, pk: int) -> JsonResponse:
    """Update (PUT) or delete (DELETE) an activity type by primary key.

    PUT accepts JSON body with ``nome`` (required).
    DELETE requires no body.

    Returns:
        JsonResponse: 200 with updated object on PUT success; 200 ``{"ok": true}``
            on DELETE success; 400/404/409 on error.
    """
    try:
        obj = TipoAttivita.objects.get(pk=pk)
    except TipoAttivita.DoesNotExist:
        return JsonResponse(
            {"ok": False, "error": "Tipo attività non trovato."}, status=404
        )

    from ui.services.operation_log import record_operation

    if request.method == "DELETE":
        nome = obj.nome
        obj.delete()
        record_operation(
            request,
            azione="anagrafica_elimina",
            descrizione=f"Eliminato tipo attività {nome}",
            riferimento=f"tipo_attivita:{pk}",
        )
        return JsonResponse({"ok": True})

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    nome = (data.get("nome") or "").strip()
    if not nome:
        return JsonResponse(
            {"ok": False, "error": "Il campo 'nome' è obbligatorio."}, status=400
        )

    if TipoAttivita.objects.filter(nome__iexact=nome).exclude(pk=pk).exists():
        return JsonResponse(
            {"ok": False, "error": f"Tipo attività '{nome}' già esistente."}, status=409
        )

    obj.nome = nome
    obj.save()
    record_operation(
        request,
        azione="anagrafica_modifica",
        descrizione=f"Aggiornato tipo attività {obj.nome}",
        riferimento=f"tipo_attivita:{obj.pk}",
    )
    return JsonResponse({"ok": True, "id": obj.pk, "nome": obj.nome})


# ── Configurazione Testa ──────────────────────────────────────────────────────


@write_required
@csrf_exempt
@require_http_methods(["POST"])
def api_config_testa_create(request: HttpRequest) -> JsonResponse:
    """Create a new ConfigurazioneTesta entry.

    Accepts JSON body with ``modello_testa`` (required), ``min_mandrini``,
    ``min_tastatori``, ``min_maschere``, ``min_generatori`` (optional,
    default 1).

    Returns:
        JsonResponse: 201 with the created object on success; 400/409 on error.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    modello = (data.get("modello_testa") or "").strip()
    if not modello:
        return JsonResponse(
            {"ok": False, "error": "Il campo 'modello_testa' è obbligatorio."},
            status=400,
        )

    if ConfigurazioneTesta.objects.filter(modello_testa__iexact=modello).exists():
        return JsonResponse(
            {"ok": False, "error": f"Configurazione per '{modello}' già esistente."},
            status=409,
        )

    def _pos_int(val, default=1):
        try:
            v = int(val)
            return v if v >= 0 else default
        except (TypeError, ValueError):
            return default

    obj = ConfigurazioneTesta.objects.create(
        modello_testa=modello,
        min_mandrini=_pos_int(data.get("min_mandrini"), 1),
        min_tastatori=_pos_int(data.get("min_tastatori"), 1),
        min_maschere=_pos_int(data.get("min_maschere"), 1),
        min_generatori=_pos_int(data.get("min_generatori"), 1),
    )

    from ui.services.operation_log import record_operation

    record_operation(
        request,
        azione="anagrafica_crea",
        descrizione=f"Creata configurazione testa {obj.modello_testa}",
        riferimento=f"config_testa:{obj.pk}",
    )

    return JsonResponse(
        {
            "ok": True,
            "id": obj.pk,
            "modello_testa": obj.modello_testa,
            "min_mandrini": obj.min_mandrini,
            "min_tastatori": obj.min_tastatori,
            "min_maschere": obj.min_maschere,
            "min_generatori": obj.min_generatori,
        },
        status=201,
    )


@write_required
@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def api_config_testa_detail(request: HttpRequest, pk: int) -> JsonResponse:
    """Update (PUT) or delete (DELETE) a ConfigurazioneTesta by primary key.

    Returns:
        JsonResponse: 200 with updated object on PUT; ``{"ok": true}`` on DELETE.
    """
    try:
        obj = ConfigurazioneTesta.objects.get(pk=pk)
    except ConfigurazioneTesta.DoesNotExist:
        return JsonResponse(
            {"ok": False, "error": "Configurazione non trovata."}, status=404
        )

    from ui.services.operation_log import record_operation

    if request.method == "DELETE":
        modello_testa = obj.modello_testa
        obj.delete()
        record_operation(
            request,
            azione="anagrafica_elimina",
            descrizione=f"Eliminata configurazione testa {modello_testa}",
            riferimento=f"config_testa:{pk}",
        )
        return JsonResponse({"ok": True})

    # PUT
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    def _pos_int(val, default):
        try:
            v = int(val)
            return v if v >= 0 else default
        except (TypeError, ValueError):
            return default

    obj.min_mandrini = _pos_int(data.get("min_mandrini"), obj.min_mandrini)
    obj.min_tastatori = _pos_int(data.get("min_tastatori"), obj.min_tastatori)
    obj.min_maschere = _pos_int(data.get("min_maschere"), obj.min_maschere)
    obj.min_generatori = _pos_int(data.get("min_generatori"), obj.min_generatori)
    obj.save()
    record_operation(
        request,
        azione="anagrafica_modifica",
        descrizione=f"Aggiornata configurazione testa {obj.modello_testa}",
        riferimento=f"config_testa:{obj.pk}",
    )
    return JsonResponse(
        {
            "ok": True,
            "id": obj.pk,
            "modello_testa": obj.modello_testa,
            "min_mandrini": obj.min_mandrini,
            "min_tastatori": obj.min_tastatori,
            "min_maschere": obj.min_maschere,
            "min_generatori": obj.min_generatori,
        }
    )


@write_required
@csrf_exempt
@require_http_methods(["POST"])
def api_gen_compatibile_create(request: HttpRequest, config_pk: int) -> JsonResponse:
    """Add a compatible generator model to a ConfigurazioneTesta.

    Accepts JSON body with ``modello_generatore`` (required).

    Returns:
        JsonResponse: 201 with ``{"ok": true, "id": <id>, "modello_generatore": "..."}``
            on success; 400/404/409 on error.
    """
    try:
        cfg = ConfigurazioneTesta.objects.get(pk=config_pk)
    except ConfigurazioneTesta.DoesNotExist:
        return JsonResponse(
            {"ok": False, "error": "Configurazione non trovata."}, status=404
        )

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    modello_gen = (data.get("modello_generatore") or "").strip()
    if not modello_gen:
        return JsonResponse(
            {"ok": False, "error": "Il campo 'modello_generatore' è obbligatorio."},
            status=400,
        )

    if cfg.generatori_compatibili.filter(
        modello_generatore__iexact=modello_gen
    ).exists():
        return JsonResponse(
            {"ok": False, "error": f"Generatore '{modello_gen}' già associato."},
            status=409,
        )

    gen = GeneratoreCompatibile.objects.create(
        configurazione=cfg, modello_generatore=modello_gen
    )

    from ui.services.operation_log import record_operation

    record_operation(
        request,
        azione="anagrafica_modifica",
        descrizione=(
            f"Aggiunto generatore compatibile {gen.modello_generatore} "
            f"a {cfg.modello_testa}"
        ),
        riferimento=f"config_testa:{cfg.pk}",
    )

    return JsonResponse(
        {"ok": True, "id": gen.pk, "modello_generatore": gen.modello_generatore},
        status=201,
    )


@write_required
@csrf_exempt
@require_http_methods(["DELETE"])
def api_gen_compatibile_detail(request: HttpRequest, pk: int) -> JsonResponse:
    """Remove a GeneratoreCompatibile entry by primary key.

    Returns:
        JsonResponse: ``{"ok": true}`` on success; 404 if not found.
    """
    try:
        gen = GeneratoreCompatibile.objects.get(pk=pk)
    except GeneratoreCompatibile.DoesNotExist:
        return JsonResponse(
            {"ok": False, "error": "Generatore non trovato."}, status=404
        )

    modello_generatore = gen.modello_generatore
    modello_testa = gen.configurazione.modello_testa
    config_pk = gen.configurazione_id
    gen.delete()

    from ui.services.operation_log import record_operation

    record_operation(
        request,
        azione="anagrafica_modifica",
        descrizione=(
            f"Rimosso generatore compatibile {modello_generatore} da {modello_testa}"
        ),
        riferimento=f"config_testa:{config_pk}",
    )

    return JsonResponse({"ok": True})


# ── Problemi attività ─────────────────────────────────────────────────────────


@login_required
@require_http_methods(["GET"])
def api_attivita_problemi(request: HttpRequest, pk: int) -> JsonResponse:
    """Return all problems associated with a specific activity.

    Args:
        request: GET request.
        pk: Primary key of the :class:`~ui.models.Attivita`.

    Returns:
        JsonResponse: ``{"ok": true, "problemi": [...]}`` where each problem
            dict contains all fields of :class:`~ui.models.ProblemaAttivita`.
            Returns 404 if the activity does not exist.
    """
    from ui.models import Attivita

    try:
        att = Attivita.objects.get(pk=pk)
    except Attivita.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Attività non trovata."}, status=404)

    problemi = list(
        att.problemi.order_by("created_at").values(
            "id",
            "tipo",
            "risorsa_tipo",
            "risorsa_uid",
            "stabilimento_origine",
            "bloccata_fino",
            "riferimento_attivita",
            "descrizione",
            "proposta_json",
            "risolto",
            "note_risoluzione",
            "data_risoluzione",
            "created_at",
        )
    )
    stab = att.stabilimento or ""
    for p in problemi:
        p["stabilimento_attivita"] = stab
        if p["data_risoluzione"]:
            p["data_risoluzione"] = p["data_risoluzione"].isoformat()
        if p["created_at"]:
            p["created_at"] = p["created_at"].isoformat()
        if p["bloccata_fino"]:
            p["bloccata_fino"] = p["bloccata_fino"].isoformat()

    return JsonResponse({"ok": True, "problemi": problemi})


@login_required
@require_http_methods(["GET"])
def api_overlap_reschedule_proposal(request: HttpRequest, pk: int) -> JsonResponse:
    """Solve a reschedule proposal for an activity, fresh at every call.

    The notes modal calls this each time it opens instead of reading a
    stored payload: any edit to another activity can make an earlier
    proposal invalid, and applying a stale one would move the activity
    into a slot that is no longer free.

    Args:
        request: GET request.
        pk: Primary key of the activity to re-plan.

    Returns:
        JsonResponse: ``{"ok": true, "proposta": {...}}``. The payload
            carries ``status`` ``proposed``, ``rejected``, ``no_proposal``
            or ``no_problem``. Read-only accounts get the proposal without
            it being stored, since they cannot apply it anyway.
    """
    from ui.permissions import has_write_access
    from ui.services.overlap_reassign import compute_reschedule_proposal

    proposta = compute_reschedule_proposal(pk, store=has_write_access(request.user))
    return JsonResponse({"ok": True, "proposta": proposta})


@write_required
@csrf_exempt
@require_http_methods(["POST"])
def api_overlap_reschedule_accept(request: HttpRequest, pk: int) -> JsonResponse:
    """Apply the stored single-activity reschedule proposal for an overlap.

    Args:
        request: POST request.
        pk: Primary key of the blocked :class:`~ui.models.Attivita`.

    Returns:
        JsonResponse: ``{"ok": true, ...}`` on success; 400 when no proposal is
            applicable; 409 when the applied schedule would conflict.
    """
    from ui.services.overlap_reassign import OverlapReassignError, apply_reschedule
    from ui.services.resource_conflicts import (
        ResourceConflictError,
        format_conflict_message,
    )

    try:
        result = apply_reschedule(pk)
    except ResourceConflictError as exc:
        return JsonResponse(
            {
                "ok": False,
                "error": format_conflict_message(exc.conflicts),
                "conflicts": exc.conflicts,
            },
            status=409,
        )
    except OverlapReassignError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=exc.status_code)

    from ui.services.operation_log import record_operation

    activity_id = (
        Attivita.objects.filter(pk=pk).values_list("activity_id", flat=True).first()
    )
    record_operation(
        request,
        azione="attivita_riprogramma_accetta",
        descrizione=(f"Proposta di riprogrammazione accettata per {activity_id or pk}"),
        riferimento=activity_id or "",
    )

    return JsonResponse({"ok": True, **result})


@write_required
@csrf_exempt
@require_http_methods(["POST"])
def api_overlap_reschedule_reject(request: HttpRequest, pk: int) -> JsonResponse:
    """Reject (dismiss) the stored reschedule proposal for an overlap."""
    from ui.services.overlap_reassign import reject_reschedule
    from ui.services.operation_log import record_operation

    result = reject_reschedule(pk)

    activity_id = (
        Attivita.objects.filter(pk=pk).values_list("activity_id", flat=True).first()
    )
    record_operation(
        request,
        azione="attivita_riprogramma_rifiuta",
        descrizione=(f"Proposta di riprogrammazione rifiutata per {activity_id or pk}"),
        riferimento=activity_id or "",
    )

    return JsonResponse({"ok": True, **result})
