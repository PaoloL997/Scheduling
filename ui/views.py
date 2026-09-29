"""Views for the ui application."""

import json
import logging
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ui.permissions import deny_if_read_only, has_write_access, write_required
from ui.services.bridge_write import (
    BridgeWriteError,
    delete_attivita,
    reconcile_delete,
    update_attivita,
)
from ui.services.importer import ImportStatus, get_error_detail, get_status
from ui.services.identifiers import normalize_activity_id, parse_job_id
from ui.services.resource_display import (
    format_activity_resources,
    format_activity_resources_html,
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
    ProblemaAttivita,
    Responsabile,
    RisorsaAttivita,
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

    The dataset is imported once at server startup via ``AppConfig.ready()``.
    The rendered template polls ``/api/status`` to detect when the import
    completes before showing the data.

    Args:
        request: The incoming HTTP request.

    The page also receives the user's own plant, which pre-selects the plant
    filter so each user starts on the activities of their stabilimento.

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

    groups = _fetch_grouped()

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


def _fetch_grouped() -> list[dict]:
    """Return activities grouped by commessa, each paired with job metadata.

    Each entry in the returned list is a dict::

        {
            "job": {commessa, job_id, delivery_date, total_cost, penalty,
                    n_activities, n_items},
            "activities": [list of task dicts from _attivita_to_task_dict],
        }

    Groups are sorted by aggregate status (late, in progress, planned,
    unplanned, completed) then by commessa name.  Commesse that appear only
    in the activity table (no matching Job row) are included with blank job
    metadata.

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
        activities.sort(
            key=lambda t: (
                _parse_iso_day(t.get("start"))
                or _parse_iso_day(t.get("actual_start"))
                or date.max,
                str(t.get("activity_id") or ""),
            )
        )
        job = {
            **job,
            "n_open_problems": _count_job_open_problems(activities),
        }
        result.append({"job": job, "activities": activities})
    result.sort(
        key=lambda g: (
            _earliest_activity_start(g["activities"]) or date.max,
            (g["job"].get("commessa") or "").lower(),
        )
    )
    return result


def _count_job_open_problems(activities: list[dict]) -> int:
    """Return open problems for a commessa (acquisto, spostamento, overlap)."""
    return sum(a.get("warning_count") or 0 for a in activities)


def _earliest_activity_start(activities: list[dict]) -> date | None:
    """Return the earliest planned (or actual) start among activities."""
    earliest: date | None = None
    for task in activities or []:
        day = _parse_iso_day(task.get("start")) or _parse_iso_day(
            task.get("actual_start")
        )
        if day is None:
            continue
        if earliest is None or day < earliest:
            earliest = day
    return earliest


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
    from ui.services.problem_detection import detect_for_all_activities

    # Recalculate acquisto/spostamento so the page reflects the current
    # schedule and resource assignments after edits.
    detect_for_all_activities()

    activities = list(
        Attivita.objects.prefetch_related("risorse", "problemi").order_by(
            "job_id", F("data_inizio").asc(nulls_first=True), "activity_id"
        )
    )
    id_to_name = {normalize_activity_id(a.activity_id): a.activity for a in activities}

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
    1. Case-insensitive lookup in id_to_name (fastest, most accurate).
    2. Parse the canonical ``commessa-item-activity`` format and URL-decode the
       activity component.
    3. Return the raw string unchanged as a last resort.

    Args:
        activity_id (str): The canonical activity identifier to resolve.
        id_to_name (dict): Mapping of normalized ``activity_id`` →
            ``activity`` for the current activity set.

    Returns:
        str: The resolved activity name, or the raw ``activity_id`` as fallback.
    """
    from urllib.parse import unquote

    key = normalize_activity_id(activity_id)
    if key in id_to_name:
        return id_to_name[key]

    parts = activity_id.split("-")
    if len(parts) >= 3:
        return unquote(parts[-1])

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
    # Still open past the planned finish: the activity is running late even
    # when it started on time.
    is_past_due = not actual_finish and today > data_fine
    # Never started and already past the planned finish: the plan is stale and
    # the activity still holds its resources.
    is_expired = not actual_start and is_past_due

    if is_completed:
        return [{"key": "completata", "label": "COMPLETATA"}]

    pills = []
    if is_late or is_overdue or is_past_due:
        pills.append({"key": "in_ritardo", "label": "IN RITARDO"})

    if is_started:
        pills.append({"key": "in_lavorazione", "label": "IN LAVORAZIONE"})
    elif is_expired:
        pills.append({"key": "scaduta", "label": "NON INIZIATA · SCADUTA"})
    elif is_overdue:
        pills.append({"key": "da_iniziare", "label": "DA INIZIARE"})
    else:
        pills.append({"key": "pianificata", "label": "PIANIFICATA"})

    return pills


_JOB_STATUS_SORT_ORDER = {
    "rischio_penale": 0,
    "in_ritardo": 1,
    "in_tempo": 2,
    "pianificata": 3,
    "non_pianificata": 4,
}


def _parse_iso_day(value):
    """Parse YYYY-MM-DD (or date) to ``date``, else None."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _activity_state_key(task: dict) -> str:
    """Return the primary status key for a task dict (mirrors client _pianActivityState)."""
    keys = [p["key"] for p in (task.get("status_pills") or [])]
    if "in_ritardo" in keys:
        return "in_ritardo"
    if "in_lavorazione" in keys or "in_lavorazione_ritardo" in keys:
        return "in_lavorazione"
    if "completata" in keys:
        return "completata"
    if "pianificata" in keys:
        return "pianificata"
    return "non_pianificata"


def _aggregate_job_status(
    activities: list[dict],
    delivery_date: str | date | None = None,
) -> str:
    """Return aggregate commessa status (same rules as Gantt / client).

    Priority: rischio_penale → in_ritardo → in_tempo → pianificata.
    """
    if not activities:
        return "non_pianificata"

    deadline = _parse_iso_day(delivery_date)
    late = 0
    started = 0
    penalty_risk = False

    for task in activities:
        state = _activity_state_key(task)
        if state == "in_ritardo":
            late += 1
        if task.get("actual_start"):
            started += 1

        if deadline is None:
            continue
        plan_s = _parse_iso_day(task.get("start"))
        plan_f = _parse_iso_day(task.get("finish"))
        act_s = _parse_iso_day(task.get("actual_start"))
        act_f = _parse_iso_day(task.get("actual_finish"))
        if plan_s is None or plan_f is None:
            continue
        dur_days = (plan_f - plan_s).days
        end = None
        if act_f is not None:
            end = act_f
        elif act_s is not None:
            end = act_s + timedelta(days=dur_days)
        if end is not None and end > deadline:
            penalty_risk = True

    if penalty_risk:
        return "rischio_penale"
    if late > 0:
        return "in_ritardo"
    if started > 0:
        return "in_tempo"
    return "pianificata"


def _is_job_completed(activities: list[dict]) -> bool:
    """True when every activity has both actual_start and actual_finish."""
    if not activities:
        return False
    return all(a.get("actual_start") and a.get("actual_finish") for a in activities)


def _has_plannable_activities(activities: list[dict]) -> bool:
    """True when at least one activity is not in progress or completed."""
    return any(
        not (a.get("actual_start") or a.get("actual_finish")) for a in activities
    )


def _attivita_to_task_dict(a) -> dict:
    """Convert an :class:`~ui.models.Attivita` instance to a pianificazione-compatible dict.

    Args:
        a: An :class:`~ui.models.Attivita` ORM instance with ``risorse`` and
           ``problemi`` prefetched.

    Returns:
        dict: A dict with keys matching the pianificazione table columns,
            including ``warning_count`` (count of unresolved problems),
            ``status_pills`` — an ordered list of status badge dicts for the
            status column — and ``min_requirements`` / ``can_start``, the
            minimum resource kit required to launch the activity and whether
            the current assignments satisfy it.
    """
    from django.utils.timezone import localdate

    from ui.services.activity_inventory import get_testa_min_requirements
    from ui.services.problem_detection import missing_minimum_resources

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

    min_requirements = get_testa_min_requirements(
        a.modello_testa or None, bool(getattr(a, "tp60_custom", False))
    )
    missing_minimum = missing_minimum_resources(
        a, [(r.tipo, r.risorsa_id) for r in risorse_all]
    )

    return {
        "activity_id": a.activity_id,
        "job_id": a.job_id,
        "commessa": a.commessa,
        "item": str(a.item),
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
        "modello_testa": a.modello_testa or None,
        "bore_min": float(a.bore_min) if a.bore_min is not None else None,
        "bore_max": float(a.bore_max) if a.bore_max is not None else None,
        "bore_mid": float(a.bore_mid) if a.bore_mid is not None else None,
        "tube_pitch": float(a.tube_pitch) if a.tube_pitch is not None else None,
        "predecessors": a.predecessors,
        "source": "attivita",
        "orm_id": a.pk,
        "warning_count": warning_count,
        "note_count": note_count,
        "status_pills": status_pills,
        "min_requirements": min_requirements,
        "min_requirements_json": json.dumps(min_requirements),
        "missing_minimum": missing_minimum,
        "can_start": not missing_minimum,
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
    from ui.services.sync_state import get_last_error, get_phase, pending_queue_count

    detail = ""
    if status == ImportStatus.ERROR:
        detail = get_error_detail()
    elif get_phase() == "dirty" or get_last_error():
        detail = get_last_error() or get_error_detail()

    return JsonResponse(
        {
            "status": status.value,
            "detail": detail,
            "pending_queue_count": pending_queue_count(),
            "sync_phase": get_phase(),
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
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    orm_id = data.get("orm_id")
    if not orm_id:
        return JsonResponse({"ok": False, "error": "orm_id mancante."}, status=400)

    try:
        result = update_attivita(int(orm_id), data)
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

    return JsonResponse({"ok": True})


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
            "mpp_path": runtime.mpp_path,
            "xlsx_path": runtime.xlsx_path,
            "server_dir": runtime.server_dir,
            "heartbeat_interval_seconds": runtime.heartbeat_interval_seconds,
            "heartbeat_timeout_seconds": runtime.heartbeat_timeout_seconds,
            "msp_dispatch_retry_interval_seconds": runtime.msp_dispatch_retry_interval_seconds,
            "msp_dispatch_retry_max_wait_seconds": runtime.msp_dispatch_retry_max_wait_seconds,
            "openai_wps_model": runtime.openai_wps_model,
            "auto_import_on_startup": runtime.auto_import_on_startup,
        }
    )


_reconcile_delete = reconcile_delete


@write_required
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

    activity_id = (
        Attivita.objects.filter(pk=int(orm_id))
        .values_list("activity_id", flat=True)
        .first()
    )

    try:
        delete_attivita(int(orm_id))
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
            att_qs = (
                Attivita.objects.filter(commessa=commessa)
                if commessa
                else Attivita.objects.filter(job_id=job_id)
            )
            if not job_qs.exists() and not att_qs.exists():
                return JsonResponse(
                    {"ok": False, "error": "Job non trovato."}, status=404
                )

            for a in att_qs:
                _reconcile_delete(a)

            att_qs.delete()
            if job_qs.exists():
                job_qs.delete()
    except Exception as exc:
        logger.error(
            "Errore eliminazione Job commessa=%s job_id=%s: %s", commessa, job_id, exc
        )
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

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
    """Return all tasks with scheduling data for the Gantt chart.

    Response shape::

        {
          "tasks": [{commessa, item, activity, duration_min, start, finish,
                     actual_start, actual_finish, baseline_start, baseline_finish,
                     plant, plant_color, responsible, resources,
                     resources_by_tipo: {testa, generatore, mandrino,
                                         tastatore, maschera: [uid]},
                     moving_resources: [uid with an open spostamento],
                     predecessors}],
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
    from ui.services.overlap_reassign import auto_resolve_resource_overlaps

    auto_resolve_resource_overlaps()

    deadlines: dict[str, str] = {
        job["job_id"]: job["delivery_date"]
        for job in Job.objects.exclude(delivery_date__isnull=True).values(
            "job_id", "delivery_date"
        )
    }

    tasks = []
    activities = list(
        Attivita.objects.prefetch_related("risorse")
        .annotate(
            open_problems=Count(
                "problemi",
                filter=Q(problemi__risolto=False),
            )
        )
        .order_by("commessa", "item", "activity")
    )
    # Resources that must be moved from another plant (open "spostamento"
    # notes), keyed by activity, so the kanban can flag their chips.
    moving: dict[int, set[str]] = {}
    for att_pk, uid in ProblemaAttivita.objects.filter(
        risolto=False, tipo=ProblemaAttivita.TIPO_SPOSTAMENTO
    ).values_list("attivita_id", "risorsa_uid"):
        if uid:
            moving.setdefault(att_pk, set()).add(uid)
    for att in activities:
        resources_str = ";".join(r.risorsa_id for r in att.risorse.all()) or None
        by_tipo: dict[str, list[str]] = {
            tipo: [] for tipo, _ in RisorsaAttivita.TIPO_CHOICES
        }
        for r in att.risorse.all():
            by_tipo.setdefault(r.tipo, []).append(r.risorsa_id)
        tasks.append(
            {
                "commessa": att.commessa,
                "item": str(att.item),
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
                "resources_by_tipo": by_tipo,
                "moving_resources": sorted(moving.get(att.pk, ())),
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
