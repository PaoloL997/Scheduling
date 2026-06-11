"""Views for the ui application."""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ui.services.importer import ImportStatus, get_error_detail, get_status
from ui.services.identifiers import parse_job_id
from ui.services.resource_display import (
    format_activity_resources,
    format_activity_resources_html,
)
from ui.services.inventory import delete_resource, insert_resource, update_resource
from ui.models import (
    AppSettings,
    Attivita,
    ConfigurazioneTesta,
    GeneratoreCompatibile,
    GeneratoreItem,
    Job,
    MandrinoItem,
    MascheraItem,
    ProblemaAttivita,
    Responsabile,
    Stabilimento,
    TastatoreItem,
    TestaItem,
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

    The dataset is imported once at server startup via ``AppConfig.ready()``.
    The rendered template polls ``/api/status`` to detect when the import
    completes before showing the data.

    Args:
        request: The incoming HTTP request.

    Returns:
        HttpResponse: The rendered index template.
    """
    return render(request, "ui/index.html")


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
    )

    groups = _fetch_grouped()

    stab_objs = list(Stabilimento.objects.values("nome", "colore"))
    stab_orm = [s["nome"] for s in stab_objs]
    resp_orm = list(Responsabile.objects.values_list("nome", flat=True))
    stabilimenti = sorted(set(stab_orm + get_stabilimenti()))
    responsabili = sorted(set(resp_orm + get_responsabili()))
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
            "penale_max_json": json.dumps(float(AppSettings.load().penale_max)),
            "testa_models_json": json.dumps(get_testa_models()),
        },
    )


@login_required
def inventario(request: HttpRequest):
    """Render the inventory page with one table per equipment category.

    Fetches all rows from each inventory table (Generatore, Mandrino,
    Maschera, Tastatore, Testa) and passes them to the template as
    separate context keys.  The active table is toggled client-side.

    Args:
        request: The incoming HTTP request.

    Returns:
        HttpResponse: The rendered inventario template.
    """
    tables = {
        "Generatore": "generatori",
        "Mandrino": "mandrini",
        "Maschera": "maschere",
        "Tastatore": "tastatori",
        "Testa": "teste",
    }
    context = {ctx_key: _fetch_inventory_table(tbl) for tbl, ctx_key in tables.items()}
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


def _fetch_grouped() -> list[dict]:
    """Return activities grouped by commessa, each paired with job metadata.

    Each entry in the returned list is a dict::

        {
            "job": {commessa, job_id, delivery_date, total_cost, penalty,
                    n_activities, n_items},
            "activities": [list of task dicts from _attivita_to_task_dict],
        }

    Groups are sorted by commessa name.  Commesse that appear only in the
    activity table (no matching Job row) are included with blank job metadata.

    Returns:
        list[dict]: One dict per commessa group.
    """
    from collections import defaultdict

    tasks = _fetch_tasks()
    jobs = _fetch_jobs()

    job_by_commessa = {j["commessa"]: j for j in jobs}

    groups_map: dict[str, list] = defaultdict(list)
    for task in tasks:
        groups_map[task["commessa"]].append(task)

    all_commesse = sorted(set(list(groups_map.keys()) + list(job_by_commessa.keys())))
    result = []
    for commessa in all_commesse:
        job = job_by_commessa.get(
            commessa,
            {
                "job_id": "",
                "commessa": commessa,
                "total_cost": None,
                "penalty": None,
                "delivery_date": None,
                "n_activities": 0,
                "n_items": 0,
            },
        )
        activities = groups_map.get(commessa, [])
        job = {
            **job,
            "n_open_problems": sum(a.get("warning_count") or 0 for a in activities),
        }
        result.append({"job": job, "activities": activities})
    return result


def _fetch_tasks() -> list[dict]:
    """Return all activity rows for the pianificazione page.

    Reads exclusively from :class:`~ui.models.Attivita`, which is the single
    source of truth for all activities — both those imported from the MS
    Project file and those added through the wizard.

    Predecessor activity_ids are resolved to human-readable activity names.
    Resolution priority:
    1. In-memory lookup from the current activity set (exact match).
    2. Parse the activity_id to extract the encoded activity component.
    3. Fall back to the raw string if parsing fails.

    Returns:
        list[dict]: One dict per activity row, compatible with the
            pianificazione table template.
    """

    from ui.models import Attivita

    activities = list(
        Attivita.objects.prefetch_related("risorse", "problemi").order_by(
            "job_id", "activity_id"
        )
    )
    id_to_name = {a.activity_id: a.activity for a in activities}

    result = []
    for a in activities:
        task = _attivita_to_task_dict(a)
        if a.predecessors:
            pred_ids = [p.strip() for p in a.predecessors.split(",") if p.strip()]
            task["predecessor_names"] = ", ".join(
                _resolve_predecessor_name(pid, id_to_name) for pid in pred_ids
            )
        else:
            task["predecessor_names"] = None
        result.append(task)
    return result


def _resolve_predecessor_name(activity_id: str, id_to_name: dict) -> str:
    """Return a short, human-readable name for a predecessor activity_id.

    Tries three strategies in order:
    1. Exact lookup in the provided id_to_name mapping (fastest, most accurate).
    2. Parse the canonical ``commessa-item-activity`` format and URL-decode the
       activity component.
    3. Return the raw string unchanged as a last resort.

    Args:
        activity_id (str): The canonical activity identifier to resolve.
        id_to_name (dict): Mapping of ``activity_id`` → ``activity`` for the
            current activity set.

    Returns:
        str: The resolved activity name, or the raw ``activity_id`` as fallback.
    """
    from urllib.parse import unquote

    if activity_id in id_to_name:
        return id_to_name[activity_id]

    parts = activity_id.split("-", 2)
    if len(parts) == 3:
        return unquote(parts[2])

    return activity_id


def _compute_activity_status(
    data_inizio,
    data_fine,
    actual_start,
    actual_finish,
    today,
) -> list[dict]:
    """Compute the ordered list of status pills for an activity.

    Mirrors the Gantt badge logic exactly so both views are always consistent.
    An activity may carry two pills simultaneously (e.g. IN RITARDO + IN LAVORAZIONE).
    When planned dates are missing the activity is considered unscheduled.

    Args:
        data_inizio: Planned start date, or None.
        data_fine: Planned finish date, or None.
        actual_start: Actual start date, or None.
        actual_finish: Actual finish date, or None.
        today: The reference date for "now" comparisons.

    Returns:
        list[dict]: Ordered list of ``{"key": str, "label": str}`` dicts,
        where *key* is the stable CSS modifier and *label* is the Italian string.
        Always contains at least one element.
    """
    if data_inizio is None or data_fine is None:
        return [{"key": "non_pianificata", "label": "NON PIANIFICATA"}]

    is_completed = bool(actual_start and actual_finish)
    is_started = bool(actual_start and not actual_finish)
    is_late = bool(actual_start and actual_start > data_inizio)
    is_overdue = not actual_start and today >= data_inizio

    if is_completed:
        return [{"key": "completata", "label": "COMPLETATA"}]

    pills = []
    if is_late or is_overdue:
        pills.append({"key": "in_ritardo", "label": "IN RITARDO"})

    if is_started:
        pills.append({"key": "in_lavorazione", "label": "IN LAVORAZIONE"})
    elif is_overdue:
        pills.append({"key": "da_iniziare", "label": "DA INIZIARE"})
    else:
        pills.append({"key": "pianificata", "label": "PIANIFICATA"})

    return pills


def _attivita_to_task_dict(a) -> dict:
    """Convert an :class:`~ui.models.Attivita` instance to a pianificazione-compatible dict.

    Args:
        a: An :class:`~ui.models.Attivita` ORM instance with ``risorse`` and
           ``problemi`` prefetched.

    Returns:
        dict: A dict with keys matching the pianificazione table columns,
            including ``warning_count`` (count of unresolved problems) and
            ``status_pills`` — an ordered list of status badge dicts for the
            status column.
    """
    from django.utils.timezone import localdate

    risorse_all = list(a.risorse.all())
    res_fmt = format_activity_resources(risorse_all)
    resources = ";".join(res_fmt["flat_ids"])
    problemi_all = list(a.problemi.all())
    warning_count = sum(1 for p in problemi_all if not p.risolto)
    note_count = sum(1 for p in problemi_all if p.risolto)

    status_pills = _compute_activity_status(
        data_inizio=a.data_inizio,
        data_fine=a.data_fine,
        actual_start=a.actual_start,
        actual_finish=a.actual_finish,
        today=localdate(),
    )

    return {
        "activity_id": a.activity_id,
        "job_id": a.job_id,
        "commessa": a.commessa,
        "item": str(a.item).zfill(2),
        "activity": a.activity,
        "duration_min": a.duration_min,
        "start": a.data_inizio.isoformat() if a.data_inizio else None,
        "finish": a.data_fine.isoformat() if a.data_fine else None,
        "actual_start": a.actual_start.isoformat() if a.actual_start else None,
        "actual_finish": a.actual_finish.isoformat() if a.actual_finish else None,
        "baseline_start": a.baseline_start.isoformat() if a.baseline_start else None,
        "baseline_finish": a.baseline_finish.isoformat() if a.baseline_finish else None,
        "plant": a.stabilimento or None,
        "responsible": a.responsabile or None,
        "resources": resources or None,
        "resources_display": res_fmt["display"],
        "resources_tooltip": res_fmt["tooltip"] or None,
        "resources_html": format_activity_resources_html(risorse_all),
        "resources_by_tipo": res_fmt["by_tipo"],
        "resources_by_tipo_json": json.dumps(res_fmt["by_tipo"]),
        "predecessors": a.predecessors,
        "source": "attivita",
        "orm_id": a.pk,
        "warning_count": warning_count,
        "note_count": note_count,
        "status_pills": status_pills,
    }


def _fetch_jobs() -> list[dict]:
    """Return all job rows enriched with activity counts.

    Reads from :class:`~ui.models.Job` (one row per commessa) and annotates
    each row with counts of distinct items and total activities from
    :class:`~ui.models.Attivita`, which is the single source of truth.

    Returns:
        list[dict]: One dict per commessa with keys: commessa, job_id,
            total_cost, penalty, delivery_date, n_activities, n_items.
    """
    from django.db.models import Count
    from ui.models import Attivita

    act_counts = {
        row["commessa"]: row
        for row in Attivita.objects.values("commessa").annotate(
            n_act=Count("id"),
            n_it=Count("item", distinct=True),
        )
    }

    jobs_by_commessa = {
        job["commessa"]: job
        for job in Job.objects.order_by("commessa").values(
            "job_id", "commessa", "total_cost", "penalty", "delivery_date"
        )
    }

    result: list[dict] = []
    all_commesse = sorted(set(list(jobs_by_commessa.keys()) + list(act_counts.keys())))
    for commessa in all_commesse:
        row = jobs_by_commessa.get(
            commessa,
            {
                "job_id": "",
                "commessa": commessa,
                "total_cost": None,
                "penalty": None,
                "delivery_date": None,
            },
        )
        counts = act_counts.get(commessa, {})
        row["n_activities"] = counts.get("n_act", 0)
        row["n_items"] = counts.get("n_it", 0)
        result.append(row)

    return result


def api_status(request: HttpRequest) -> JsonResponse:
    """Return the current database import status as JSON.

    Response schema::

        {
            "status": "loading" | "ready" | "error" | "writing",
            "detail": "<error message or empty string>"
        }

    The ``"writing"`` status indicates that a write-back to the source files
    (``.mpp`` and ``.xlsx``) is in progress.  The frontend should display a
    blocking overlay and disable all user interactions until the status
    transitions away from ``"writing"``.

    Args:
        request: The incoming HTTP request.

    Returns:
        JsonResponse: A JSON object with ``status`` and ``detail`` fields.
    """
    status = get_status()
    return JsonResponse(
        {
            "status": status.value,
            "detail": get_error_detail() if status == ImportStatus.ERROR else "",
        }
    )


@login_required
def api_heartbeat(request: HttpRequest) -> JsonResponse:
    """Refresh the heartbeat timestamp for the authenticated session.

    Called by the frontend every 30 seconds to signal that the browser page is
    still open.  Uses :meth:`~ui.services.project_session.ProjectSession.register_session`
    rather than the lighter :meth:`~heartbeat` so that a page reload after a
    cleanup cycle (session removed from the active set) automatically
    re-registers the session and restarts the import pipeline — preventing the
    Gantt from remaining empty until the next explicit login.

    :meth:`register_session` is idempotent when the session is already tracked:
    it simply refreshes the last-seen timestamp without spawning a second hold
    thread.

    Args:
        request: The incoming HTTP request (must be authenticated).

    Returns:
        JsonResponse: ``{"ok": true}``.
    """
    from ui.services.project_session import project_session

    if request.session.session_key:
        project_session.register_session(request.session.session_key)
    return JsonResponse({"ok": True})


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

    return JsonResponse({"ok": True, "uid": uid}, status=201)


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def api_inventario_detail(request: HttpRequest, table: str, uid: str) -> JsonResponse:
    """Update (PUT) or delete (DELETE) a resource by its uid.

    PUT accepts a JSON body whose keys correspond to the columns of the target
    table.  The ``uid`` field is required; it may differ from the URL ``uid``
    parameter to support renaming the primary key.

    Response schema (success)::

        {"ok": true, "uid": "<new_uid>"}   # PUT
        {"ok": true}                        # DELETE

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
        return JsonResponse({"ok": True})

    # PUT
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

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

    return JsonResponse({"ok": True, "uid": new_uid})


@csrf_exempt
@require_http_methods(["PUT"])
def api_tasks_update(request: HttpRequest) -> JsonResponse:
    """Update a single Attivita record and enqueue a write-back entry.

    Looks up the :class:`~ui.models.Attivita` record by ``orm_id`` and
    applies any supplied subset of updatable fields.  After a successful
    save, an ``update_task`` entry is appended to
    :class:`~ui.models.MppSyncQueue` so the change is propagated to the
    MS Project file on the next logout.

    Args:
        request: The incoming HTTP PUT request with a JSON body containing
            ``orm_id`` and any combination of ``start``, ``finish``,
            ``actual_start``, ``actual_finish``, ``plant``, ``responsible``.

    Returns:
        JsonResponse: 200 ``{"ok": true}`` on success; 400/404/500 on error.
    """
    import datetime

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    orm_id = data.get("orm_id")
    if not orm_id:
        return JsonResponse({"ok": False, "error": "orm_id mancante."}, status=400)

    try:
        from ui.models import Attivita, MppSyncQueue

        a = Attivita.objects.get(pk=orm_id)

        # Capture original identity fields for MppSyncQueue write-back.
        old_commessa = a.commessa
        old_item = a.item
        old_activity = a.activity

        def _parse_date(val):
            """Parse an ISO date string or return None on failure."""
            if val:
                try:
                    return datetime.date.fromisoformat(val)
                except ValueError:
                    pass
            return None

        # Identity field changes (commessa, item, activity)
        if "commessa" in data:
            new_commessa = (data["commessa"] or "").strip()
            if new_commessa:
                a.commessa = new_commessa
        if "item" in data:
            try:
                a.item = int(data["item"])
            except (ValueError, TypeError):
                return JsonResponse(
                    {
                        "ok": False,
                        "error": "Il valore di item non è un numero intero valido.",
                    },
                    status=400,
                )
        if "activity" in data:
            a.activity = (data["activity"] or "").strip()

        # Uniqueness check after applying identity changes.
        if (
            a.commessa != old_commessa
            or a.item != old_item
            or a.activity != old_activity
        ):
            if (
                Attivita.objects.filter(
                    commessa=a.commessa, item=a.item, activity=a.activity
                )
                .exclude(pk=a.pk)
                .exists()
            ):
                return JsonResponse(
                    {
                        "ok": False,
                        "error": (
                            "Esiste già un'attività con questa combinazione "
                            "commessa/item/attività."
                        ),
                    },
                    status=409,
                )

        if "start" in data:
            a.data_inizio = _parse_date(data["start"])
        if "finish" in data:
            a.data_fine = _parse_date(data["finish"])
        if "actual_start" in data:
            a.actual_start = _parse_date(data["actual_start"])
        if "actual_finish" in data:
            a.actual_finish = _parse_date(data["actual_finish"])
        if "plant" in data:
            a.stabilimento = data["plant"] or ""
        if "responsible" in data:
            a.responsabile = data["responsible"] or ""
        if "predecessors" in data:
            predecessor_ids = [
                str(v).strip() for v in data["predecessors"] if str(v).strip()
            ]
            if predecessor_ids:
                valid = set(
                    Attivita.objects.filter(commessa=a.commessa, item=a.item)
                    .exclude(pk=a.pk)
                    .values_list("activity_id", flat=True)
                )
                invalid = [p for p in predecessor_ids if p not in valid]
                if invalid:
                    return JsonResponse(
                        {
                            "ok": False,
                            "error": f"Predecessor non valido: {', '.join(invalid)}",
                        },
                        status=400,
                    )
            a.predecessors = ", ".join(predecessor_ids) if predecessor_ids else None

        if ("start" in data or "finish" in data) and a.data_inizio and a.data_fine:
            from ui.models import RisorsaAttivita
            from ui.services.resource_conflicts import (
                ResourceConflictError,
                validate_no_conflicts,
            )

            existing_pairs = list(
                RisorsaAttivita.objects.filter(attivita_id=a.pk).values_list(
                    "tipo", "risorsa_id"
                )
            )
            try:
                validate_no_conflicts(
                    existing_pairs,
                    a.data_inizio,
                    a.data_fine,
                    exclude_activity_id=a.pk,
                )
            except ResourceConflictError as exc:
                return JsonResponse(
                    {
                        "ok": False,
                        "error": "Conflitto risorse",
                        "conflicts": exc.conflicts,
                    },
                    status=409,
                )

        a.save()

        MppSyncQueue.objects.create(
            operation="update_task",
            payload_json=json.dumps(
                {
                    "attivita_id": orm_id,
                    # New identity (may have changed).
                    "commessa": a.commessa,
                    "item": a.item,
                    "activity": a.activity,
                    # Old identity so write-back can locate the MPP task by name.
                    "old_commessa": old_commessa,
                    "old_item": old_item,
                    "old_activity": old_activity,
                    "start": a.data_inizio.isoformat() if a.data_inizio else None,
                    "finish": a.data_fine.isoformat() if a.data_fine else None,
                    "actual_start": a.actual_start.isoformat()
                    if a.actual_start
                    else None,
                    "actual_finish": a.actual_finish.isoformat()
                    if a.actual_finish
                    else None,
                    "plant": a.stabilimento or None,
                    "responsible": a.responsabile or None,
                }
            ),
        )
    except Attivita.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Attività non trovata."}, status=404)
    except Exception as exc:
        logger.error("Errore aggiornamento Attivita pk=%s: %s", orm_id, exc)
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return JsonResponse(
        {
            "ok": True,
            "item": a.item,
            "job_id": a.job_id,
            "activity_id": a.activity_id,
        }
    )


@login_required
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
        penale_max = float(AppSettings.load().penale_max)
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


@login_required
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
        penale_max = float(AppSettings.load().penale_max)
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

    return JsonResponse({"ok": True})


@login_required
def api_jobs_check(request: HttpRequest) -> JsonResponse:
    """Check whether a job (commessa) already exists in the database.

    Used by the add-activity wizard after WPS analysis to determine
    whether the user needs to create a new commessa or can proceed.

    Args:
        request: GET request with ``commessa`` query parameter.

    Returns:
        JsonResponse: ``{"ok": True, "exists": bool}`` where ``exists``
        is ``True`` when a :class:`~ui.models.Job` with the given
        ``commessa`` value is already present.
    """
    commessa = (request.GET.get("commessa") or "").strip()
    job_id = (request.GET.get("job_id") or "").strip()
    item_raw = request.GET.get("item") or "1"
    if not commessa and not job_id:
        return JsonResponse(
            {"ok": False, "error": "commessa (o job_id) è obbligatoria."}, status=400
        )
    try:
        item = int(item_raw)
    except (TypeError, ValueError):
        item = 1
    exists = (
        Job.objects.filter(commessa=commessa).exists()
        if commessa
        else Job.objects.filter(job_id=job_id).exists()
    )
    predecessor_qs = Attivita.objects.filter(commessa=commessa, item=item).order_by(
        "data_inizio", "id"
    )
    predecessors = [
        {
            "id": a.pk,
            "activity_id": a.activity_id,
            "activity": a.activity,
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
    """Read (GET) or update (PUT) the application-wide settings.

    Currently manages only ``penale_max``.

    Args:
        request: The incoming HTTP request.

    Returns:
        JsonResponse: ``{"ok": true, "penale_max": <value>}`` on GET/success;
            ``{"ok": false, "error": "..."}`` on validation error.
    """
    settings_obj = AppSettings.load()

    if request.method == "GET":
        return JsonResponse(
            {
                "ok": True,
                "penale_max": float(settings_obj.penale_max),
                "solver_time_limit": float(settings_obj.solver_time_limit),
            }
        )

    # PUT
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    from decimal import Decimal, InvalidOperation

    updated = False
    if "penale_max" in data:
        try:
            penale_max_val = float(data["penale_max"])
        except (ValueError, TypeError):
            return JsonResponse(
                {"ok": False, "error": "Valore penale_max non valido."}, status=400
            )
        if penale_max_val < 0 or penale_max_val > 100:
            return JsonResponse(
                {"ok": False, "error": "penale_max deve essere compreso tra 0 e 100."},
                status=400,
            )
        settings_obj.penale_max = Decimal(str(penale_max_val))
        updated = True

    if "solver_time_limit" in data:
        try:
            limit_val = float(data["solver_time_limit"])
        except (ValueError, TypeError):
            return JsonResponse(
                {"ok": False, "error": "Valore solver_time_limit non valido."},
                status=400,
            )
        if limit_val < 1 or limit_val > 600:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "solver_time_limit deve essere tra 1 e 600 secondi.",
                },
                status=400,
            )
        settings_obj.solver_time_limit = limit_val
        updated = True

    if not updated:
        return JsonResponse(
            {"ok": False, "error": "Nessun parametro da aggiornare."}, status=400
        )

    try:
        settings_obj.save()
    except (InvalidOperation, Exception) as exc:
        logger.error("Errore salvataggio AppSettings: %s", exc)
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return JsonResponse(
        {
            "ok": True,
            "penale_max": float(settings_obj.penale_max),
            "solver_time_limit": float(settings_obj.solver_time_limit),
        }
    )


# ---------------------------------------------------------------------------
# Queue reconciliation helpers
# ---------------------------------------------------------------------------


def _cancel_pending_task_ops(attivita_id: int) -> None:
    """Remove ``add_task`` and ``update_task`` queue entries for an activity.

    Called when an activity is deleted before its pending write-back has been
    applied to the ``.mpp`` file.  Prevents ghost tasks from being created or
    updated at the next logout.

    Args:
        attivita_id: Primary key of the :class:`~ui.models.Attivita` whose
            pending queue entries should be removed.
    """
    from ui.models import MppSyncQueue

    for entry in MppSyncQueue.objects.filter(operation__in=["add_task", "update_task"]):
        try:
            payload = json.loads(entry.payload_json)
        except (json.JSONDecodeError, ValueError):
            continue
        if payload.get("attivita_id") == attivita_id:
            entry.delete()


def _reconcile_delete(attivita) -> None:
    """Reconcile :class:`~ui.models.MppSyncQueue` when an activity is about to be deleted.

    Applies the following rules based on the activity's current sync state:

    * ``synced_to_mpp=True``: the task exists in the ``.mpp`` file.  Cancel any
      pending ``add_task`` / ``update_task`` entries and enqueue ``delete_task``
      so the task is removed at the next logout.
    * ``synced_to_mpp=False``: the task has never been written to the ``.mpp``
      file.  Cancel any pending ``add_task`` / ``update_task`` entries so no
      ghost task is created at logout.  No ``delete_task`` is enqueued.

    Must be called **before** the activity is deleted from the database, and
    within the same ``transaction.atomic()`` block as the deletion.

    Args:
        attivita: The :class:`~ui.models.Attivita` instance about to be deleted.
    """
    from ui.models import MppSyncQueue

    _cancel_pending_task_ops(attivita.pk)

    if attivita.synced_to_mpp:
        MppSyncQueue.objects.create(
            operation="delete_task",
            payload_json=json.dumps(
                {
                    "commessa": attivita.commessa,
                    "item": attivita.item,
                    "activity": attivita.activity,
                }
            ),
        )


@csrf_exempt
@require_http_methods(["DELETE"])
def api_attivita_delete(request: HttpRequest) -> JsonResponse:
    """Delete a single Attivita record and reconcile the MppSyncQueue.

    Reconciliation rules based on the activity's sync state:

    * ``synced_to_mpp=True`` (task exists in the ``.mpp`` file) — cancel any
      pending ``update_task`` entries for the activity and enqueue
      ``delete_task`` so the task is removed at the next logout.
    * ``synced_to_mpp=False`` (task not yet in the ``.mpp`` file) — cancel any
      pending ``add_task`` / ``update_task`` entries so no ghost task is
      created at logout; no ``delete_task`` is enqueued.

    The DB deletion and queue reconciliation are performed atomically so the
    system never ends up in a state where the record is gone but the queue is
    inconsistent.

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

    try:
        from ui.models import Attivita

        a = Attivita.objects.get(pk=orm_id)
        with transaction.atomic():
            _reconcile_delete(a)
            a.delete()
    except Attivita.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Attività non trovata."}, status=404)
    except Exception as exc:
        logger.error("Errore eliminazione Attivita pk=%s: %s", orm_id, exc)
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return JsonResponse({"ok": True})


@csrf_exempt
@require_http_methods(["DELETE"])
def api_jobs_delete(request: HttpRequest) -> JsonResponse:
    """Delete a job and all its associated activities, reconciling the MppSyncQueue.

    For each :class:`~ui.models.Attivita` belonging to the commessa, applies
    the same reconciliation rules as :func:`api_attivita_delete`:

    * Activities with ``synced_to_mpp=True`` have pending ``update_task``
      entries cancelled and a ``delete_task`` entry enqueued.
    * Activities with ``synced_to_mpp=False`` have pending
      ``add_task`` / ``update_task`` entries cancelled without enqueueing
      anything (the task never reached the ``.mpp`` file).

    The Job row, all Attivita rows, and all queue mutations are applied
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
            if not job_qs.exists():
                return JsonResponse(
                    {"ok": False, "error": "Job non trovato."}, status=404
                )

            att_qs = (
                Attivita.objects.filter(commessa=commessa)
                if commessa
                else Attivita.objects.filter(job_id=job_id)
            )
            for a in att_qs:
                _reconcile_delete(a)

            att_qs.delete()
            job_qs.delete()
    except Exception as exc:
        logger.error(
            "Errore eliminazione Job commessa=%s job_id=%s: %s", commessa, job_id, exc
        )
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return JsonResponse({"ok": True})


@login_required
def api_gantt(request: HttpRequest) -> JsonResponse:
    """Return all tasks with scheduling data for the Gantt chart.

    Response shape::

        {
          "tasks": [{commessa, item, activity, duration_min, start, finish,
                     actual_start, actual_finish, baseline_start, baseline_finish,
                     plant, plant_color, responsible, resources, predecessors}],
          "deadlines": {"job_id": "YYYY-MM-DD"}
        }

    Args:
        request: The incoming GET request.
    """
    plant_colors = {s.nome: s.colore for s in Stabilimento.objects.all()}
    tasks, deadlines = _load_gantt_data(plant_colors)
    return JsonResponse({"tasks": tasks, "deadlines": deadlines})


def _load_gantt_data(plant_colors: dict) -> tuple[list, dict]:
    """Query the Django ORM and build the tasks + deadlines payload.

    All activities are read exclusively from :class:`~ui.models.Attivita`,
    which is the single source of truth for both imported and wizard-created
    activities.

    Args:
        plant_colors: Mapping of plant name → hex colour string.

    Returns:
        Tuple of ``(tasks list, deadlines dict)``.
    """
    from ui.models import Attivita

    deadlines: dict[str, str] = {
        job["job_id"]: job["delivery_date"]
        for job in Job.objects.exclude(delivery_date__isnull=True).values(
            "job_id", "delivery_date"
        )
    }

    tasks = []
    for att in (
        Attivita.objects.prefetch_related("risorse")
        .annotate(open_problems=Count("problemi", filter=Q(problemi__risolto=False)))
        .order_by("commessa", "item", "activity")
    ):
        resources_str = ";".join(r.risorsa_id for r in att.risorse.all()) or None
        tasks.append(
            {
                "commessa": att.commessa,
                "item": str(att.item).zfill(2),
                "job_id": att.job_id,
                "activity_id": att.activity_id,
                "activity": att.activity or "",
                "duration_min": att.duration_min,
                "start": att.data_inizio.isoformat() if att.data_inizio else None,
                "finish": att.data_fine.isoformat() if att.data_fine else None,
                "actual_start": att.actual_start.isoformat()
                if att.actual_start
                else None,
                "actual_finish": att.actual_finish.isoformat()
                if att.actual_finish
                else None,
                "baseline_start": att.baseline_start.isoformat()
                if att.baseline_start
                else None,
                "baseline_finish": att.baseline_finish.isoformat()
                if att.baseline_finish
                else None,
                "plant": att.stabilimento or None,
                "plant_color": plant_colors.get(att.stabilimento or "", "#4a90e2"),
                "responsible": att.responsabile or None,
                "resources": resources_str,
                "predecessors": att.predecessors,
                "orm_id": att.pk,
                "warning_count": att.open_problems,
            }
        )

    return tasks, deadlines


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

    return render(
        request,
        "ui/impostazioni.html",
        {
            "stabilimenti": list(Stabilimento.objects.values("id", "nome", "colore")),
            "responsabili": list(Responsabile.objects.values("id", "nome")),
            "configurazioni_teste": configurazioni_teste,
            "all_testa_models_json": json.dumps(all_testa_models),
            "all_gen_models_json": json.dumps(all_gen_models),
            "penale_max": float(AppSettings.load().penale_max),
            "solver_time_limit": int(AppSettings.load().solver_time_limit),
        },
    )


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
    return JsonResponse(
        {"ok": True, "id": obj.pk, "nome": obj.nome, "colore": obj.colore}, status=201
    )


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

    if request.method == "DELETE":
        obj.delete()
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
    return JsonResponse(
        {"ok": True, "id": obj.pk, "nome": obj.nome, "colore": obj.colore}
    )


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
    return JsonResponse({"ok": True, "id": obj.pk, "nome": obj.nome}, status=201)


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

    if request.method == "DELETE":
        obj.delete()
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
    return JsonResponse({"ok": True, "id": obj.pk, "nome": obj.nome})


# ── Configurazione Testa ──────────────────────────────────────────────────────


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

    if request.method == "DELETE":
        obj.delete()
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
    return JsonResponse(
        {"ok": True, "id": gen.pk, "modello_generatore": gen.modello_generatore},
        status=201,
    )


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

    gen.delete()
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
            "descrizione",
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

    return JsonResponse({"ok": True, "problemi": problemi})


@login_required
@csrf_exempt
@require_http_methods(["PATCH"])
def api_problema_risolvi(request: HttpRequest, pk: int) -> JsonResponse:
    """Mark a single ProblemaAttivita as resolved.

    Accepts an optional JSON body with ``note`` (free-text resolution notes).
    Sets ``risolto=True`` and records the current timestamp.

    Args:
        request: PATCH request with optional JSON body ``{"note": "..."}``.
        pk: Primary key of the :class:`~ui.models.ProblemaAttivita`.

    Returns:
        JsonResponse: ``{"ok": true, "warning_count": N}`` where
            ``warning_count`` is the remaining number of unresolved problems
            for the same activity.  Returns 404 if not found.
    """
    from django.utils import timezone

    try:
        problema = ProblemaAttivita.objects.select_related("attivita").get(pk=pk)
    except ProblemaAttivita.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Problema non trovato."}, status=404)

    try:
        body = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = {}

    note = (body.get("note") or "").strip()
    problema.risolto = True
    problema.note_risoluzione = note
    problema.data_risoluzione = timezone.now()
    problema.save(update_fields=["risolto", "note_risoluzione", "data_risoluzione"])

    # Persist resolution note in RisoluzioneProblema so it survives re-imports.
    from ui.models import RisoluzioneProblema

    RisoluzioneProblema.objects.update_or_create(
        commessa=problema.attivita.commessa,
        item=problema.attivita.item,
        activity=problema.attivita.activity,
        tipo=problema.tipo,
        risorsa_tipo=problema.risorsa_tipo,
        risorsa_uid=problema.risorsa_uid,
        defaults={
            "note_risoluzione": note,
            "data_risoluzione": problema.data_risoluzione,
        },
    )

    remaining = ProblemaAttivita.objects.filter(
        attivita=problema.attivita, risolto=False
    ).count()
    resolved = ProblemaAttivita.objects.filter(
        attivita=problema.attivita, risolto=True
    ).count()

    return JsonResponse(
        {"ok": True, "warning_count": remaining, "note_count": resolved}
    )
