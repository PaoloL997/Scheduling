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
from ui.services.inventory import delete_resource, insert_resource, update_resource
from ui.models import (
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
    """Render the planning page with tasks and jobs tables.

    Fetches data from both the ``Tasks`` and ``Jobs`` tables and passes
    them to the template.  The active view (attività vs commesse) is
    controlled client-side via a toggle switch; both datasets are sent
    in one render to avoid extra round-trips.

    Args:
        request: The incoming HTTP request.

    Returns:
        HttpResponse: The rendered pianificazione template.
    """
    from ui.services.activity_inventory import get_responsabili, get_stabilimenti

    tasks = _fetch_tasks()
    jobs = _fetch_jobs()

    # Combine ORM-managed and imported-database sources for the wizard dropdowns.
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
            "tasks": tasks,
            "jobs": jobs,
            "stabilimenti": stabilimenti,
            "stabilimenti_json": json.dumps(stabilimenti),
            "plant_colors_json": json.dumps(plant_colors),
            "responsabili": responsabili,
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


def _fetch_tasks() -> list[dict]:
    """Return all activity rows for the pianificazione page.

    Reads exclusively from :class:`~ui.models.Attivita`, which is the single
    source of truth for all activities — both those imported from the MS
    Project file and those added through the wizard.

    Returns:
        list[dict]: One dict per activity row, compatible with the
            pianificazione table template.
    """
    from ui.models import Attivita

    return [
        _attivita_to_task_dict(a)
        for a in Attivita.objects.prefetch_related("risorse", "problemi").order_by(
            "commessa", "item", "activity"
        )
    ]


def _attivita_to_task_dict(a) -> dict:
    """Convert an :class:`~ui.models.Attivita` instance to a pianificazione-compatible dict.

    Args:
        a: An :class:`~ui.models.Attivita` ORM instance with ``risorse`` and
           ``problemi`` prefetched.

    Returns:
        dict: A dict with keys matching the pianificazione table columns,
            including ``warning_count`` (count of unresolved problems).
    """
    resources = ";".join(r.risorsa_id for r in a.risorse.all())
    problemi_all = list(a.problemi.all())
    warning_count = sum(1 for p in problemi_all if not p.risolto)
    note_count = sum(1 for p in problemi_all if p.risolto)
    return {
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
        "predecessors": a.predecessors,
        "source": "attivita",
        "orm_id": a.pk,
        "warning_count": warning_count,
        "note_count": note_count,
    }


def _fetch_jobs() -> list[dict]:
    """Return all job rows enriched with activity counts.

    Reads from :class:`~ui.models.Job` and annotates each row with
    counts of distinct items and total activities from
    :class:`~ui.models.Attivita`, which is the single source of truth.

    Returns:
        list[dict]: One dict per job row with keys: commessa, total_cost,
            penalty, delivery_date, n_activities, n_items.
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

    result = []
    for job in Job.objects.order_by("commessa").values(
        "commessa", "total_cost", "penalty", "delivery_date"
    ):
        job = dict(job)
        counts = act_counts.get(job["commessa"], {})
        job["n_activities"] = counts.get("n_act", 0)
        job["n_items"] = counts.get("n_it", 0)
        result.append(job)
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

        def _parse_date(val):
            """Parse an ISO date string or return None on failure."""
            if val:
                try:
                    return datetime.date.fromisoformat(val)
                except ValueError:
                    pass
            return None

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
                    "commessa": a.commessa,
                    "item": a.item,
                    "activity": a.activity,
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

    return JsonResponse({"ok": True})


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
    if not commessa:
        return JsonResponse(
            {"ok": False, "error": "commessa è obbligatoria."}, status=400
        )

    try:
        updated = Job.objects.filter(commessa=commessa).update(
            total_cost=data.get("total_cost") or None,
            penalty=data.get("penalty") or None,
            delivery_date=data.get("delivery_date") or None,
        )
        if updated == 0:
            return JsonResponse(
                {"ok": False, "error": "Commessa non trovata."}, status=404
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
    if not commessa:
        return JsonResponse(
            {"ok": False, "error": "commessa è obbligatoria."}, status=400
        )
    exists = Job.objects.filter(commessa=commessa).exists()
    return JsonResponse({"ok": True, "exists": exists})


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
    if not commessa:
        return JsonResponse(
            {"ok": False, "error": "commessa è obbligatoria."}, status=400
        )

    try:
        from ui.models import Attivita

        with transaction.atomic():
            if not Job.objects.filter(commessa=commessa).exists():
                return JsonResponse(
                    {"ok": False, "error": "Commessa non trovata."}, status=404
                )

            for a in Attivita.objects.filter(commessa=commessa):
                _reconcile_delete(a)

            Attivita.objects.filter(commessa=commessa).delete()
            Job.objects.filter(commessa=commessa).delete()
    except Exception as exc:
        logger.error("Errore eliminazione Job commessa=%s: %s", commessa, exc)
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
          "deadlines": {"commessa": "YYYY-MM-DD"}
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
        job["commessa"]: job["delivery_date"]
        for job in Job.objects.exclude(delivery_date__isnull=True).values(
            "commessa", "delivery_date"
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

        {"commessa": "25033", "deadline": "2026-12-31"}   # set
        {"commessa": "25033", "deadline": null}            # clear

    Args:
        request: The incoming request.
    """
    if request.method == "POST":
        return _handle_deadline_update(request)
    rows = Job.objects.values("commessa", "delivery_date")
    return JsonResponse({r["commessa"]: r["delivery_date"] for r in rows})


def _handle_deadline_update(request: HttpRequest) -> JsonResponse:
    """Parse the POST body and update a single deadline via the Job ORM model.

    Args:
        request: A POST request with a JSON body.

    Returns:
        ``{"ok": True}`` on success, or ``{"ok": False, "error": ...}`` on failure.
    """
    try:
        data = json.loads(request.body)
        commessa = data.get("commessa", "").strip()
        deadline = data.get("deadline")
        if commessa:
            if deadline:
                Job.objects.update_or_create(
                    commessa=commessa,
                    defaults={"delivery_date": deadline},
                )
            else:
                Job.objects.filter(commessa=commessa).update(delivery_date=None)
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
    all_testa_models = sorted(
        set(
            TestaItem.objects.exclude(model__isnull=True)
            .exclude(model="")
            .values_list("model", flat=True)
        )
    )
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
