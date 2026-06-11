"""Build SchedulingProblem instances from Django ORM data for the UI."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from src.optimization.calendar import WorkingCalendar, working_days_in_calendar_span
from src.optimization.compatibility import configure_generator_compatibility
from src.optimization.data_model import (
    HEAD_MODELS,
    PLANTS,
    Activity,
    Job,
    Resource,
    ResourceReservation,
    ResourceType,
    SchedulingProblem,
)
from src.optimization.output_model import ScheduleResult

from ui.models import (
    Attivita,
    ConfigurazioneTesta,
    GeneratoreCompatibile,
    GeneratoreItem,
    Job as JobModel,
    MandrinoItem,
    MascheraItem,
    RisorsaAttivita,
    TastatoreItem,
    TestaItem,
)

MINUTES_PER_WORKING_DAY = 8 * 60

_DB_PROBLEM_ACTIVITY_ID_RE = re.compile(
    r"^(?P<commessa>.+)-(?P<item>\d+)-(?P<activity>[A-Za-z0-9_]+)-(?P<pk>\d+)(?:-c\d+)?$"
)


def _parse_db_problem_activity_id(
    activity_id: str,
) -> tuple[str, int, str, int] | None:
    """Parse activity ids produced by ``src.optimization.db_problem``."""
    match = _DB_PROBLEM_ACTIVITY_ID_RE.match(activity_id or "")
    if not match:
        return None
    return (
        match.group("commessa"),
        int(match.group("item")),
        match.group("activity").replace("_", " "),
        int(match.group("pk")),
    )


def match_attivita_for_solver_activity_id(
    solver_activity_id: str,
    attivita: Iterable[Attivita],
) -> Attivita | None:
    """Resolve a solver ``activity_id`` to an ORM ``Attivita`` row."""
    rows = list(attivita)
    by_id = {att.activity_id: att for att in rows}
    if solver_activity_id in by_id:
        return by_id[solver_activity_id]

    by_pk = {att.pk: att for att in rows}
    by_triple = {(att.commessa, att.item, att.activity): att for att in rows}

    parsed_db = _parse_db_problem_activity_id(solver_activity_id)
    if parsed_db is not None:
        commessa, item, activity, pk = parsed_db
        if pk in by_pk:
            return by_pk[pk]
        hit = by_triple.get((commessa, item, activity))
        if hit is not None:
            return hit

    try:
        from ui.services.identifiers import parse_activity_id

        commessa, item, activity = parse_activity_id(solver_activity_id)
    except ValueError:
        return None
    return by_triple.get((commessa, item, activity))


class OptimizationProblemError(ValueError):
    """Raised when the selected activities cannot form a valid problem."""


def _activity_matches_job(att: Attivita, job_id: str) -> bool:
    """Return whether an activity belongs to the canonical job id."""
    from ui.services.identifiers import build_job_id, parse_job_id

    commessa = parse_job_id(job_id)
    canonical = build_job_id(commessa)
    return (
        att.commessa == commessa
        or att.job_id in {job_id, canonical}
        or (att.job_id or "").startswith(f"{canonical}-")
    )


def _job_ids_query(job_ids: list[str]) -> Any:
    """Build an ORM Q object matching all activities for the given job ids."""
    from django.db.models import Q

    from ui.services.identifiers import build_job_id, parse_job_id

    query = Q()
    for job_id in job_ids:
        commessa = parse_job_id(job_id)
        canonical = build_job_id(commessa)
        query |= Q(commessa=commessa)
        query |= Q(job_id=job_id)
        query |= Q(job_id=canonical)
        query |= Q(job_id__startswith=f"{canonical}-")
    return query


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _plant(value: str | None) -> str:
    text = (value or "").strip()
    if text in PLANTS:
        return text
    return "Valbrembo"


def _duration_days(
    duration_min: int | None, start: date | None, end: date | None
) -> int:
    if start is not None and end is not None:
        return working_days_in_calendar_span(start, end)
    if duration_min and duration_min > 0:
        return max(1, math.ceil(duration_min / MINUTES_PER_WORKING_DAY))
    return 1


def _penalty_per_week(job: JobModel | None) -> float:
    if job is None:
        return 0.0
    total_cost = job.total_cost
    penalty = job.penalty
    if total_cost and penalty and total_cost > 0 and penalty > 0:
        return total_cost * penalty / 100
    if penalty and penalty > 0:
        return penalty
    return 0.0


def _due_date(job: JobModel | None, activities: list[Attivita]) -> date:
    if job is not None and job.delivery_date:
        try:
            return date.fromisoformat(str(job.delivery_date)[:10])
        except ValueError:
            pass
    finishes = [a.data_fine for a in activities if a.data_fine is not None]
    if finishes:
        return max(finishes)
    return date.today()


def _fetch_required_quantities() -> dict[str, dict[ResourceType, int]]:
    quantities: dict[str, dict[ResourceType, int]] = {}
    for cfg in ConfigurazioneTesta.objects.all():
        quantities[cfg.modello_testa] = {
            ResourceType.MANDRINO: max(1, cfg.min_mandrini),
            ResourceType.TASTATORE: max(1, cfg.min_tastatori),
            ResourceType.MASCHERA: max(1, cfg.min_maschere),
            ResourceType.GENERATORE: max(1, cfg.min_generatori),
            ResourceType.TESTA: 1,
        }
    return quantities


def _fetch_generator_compatibility() -> dict[str, list[str]]:
    compatibility: dict[str, list[str]] = defaultdict(list)
    for row in GeneratoreCompatibile.objects.select_related("configurazione"):
        head_model = (row.configurazione.modello_testa or "").strip()
        generator_model = (row.modello_generatore or "").strip()
        if head_model and generator_model:
            compatibility[head_model].append(generator_model)
    return dict(compatibility)


def _fetch_inventory() -> list[Resource]:
    resources: list[Resource] = []

    for row in MandrinoItem.objects.all():
        bore = _parse_float(row.diameter)
        if bore is not None and bore > 0:
            resources.append(
                Resource(
                    resource_id=f"mandrino:{row.uid}",
                    resource_type=ResourceType.MANDRINO,
                    plant=_plant(row.plant),
                    bore=bore,
                )
            )

    for row in TastatoreItem.objects.all():
        bore = _parse_float(row.diameter)
        if bore is not None and bore > 0:
            resources.append(
                Resource(
                    resource_id=f"tastatore:{row.uid}",
                    resource_type=ResourceType.TASTATORE,
                    plant=_plant(row.plant),
                    bore=bore,
                )
            )

    for row in MascheraItem.objects.all():
        pitch = _parse_float(row.pitch)
        if pitch is not None and pitch > 0:
            resources.append(
                Resource(
                    resource_id=f"maschera:{row.uid}",
                    resource_type=ResourceType.MASCHERA,
                    plant=_plant(row.plant),
                    tube_pitch=pitch,
                )
            )

    for row in TestaItem.objects.all():
        model = (row.model or "").strip() or None
        if model is not None:
            resources.append(
                Resource(
                    resource_id=f"testa:{row.uid}",
                    resource_type=ResourceType.TESTA,
                    plant=_plant(row.plant),
                    model=model,
                )
            )

    for row in GeneratoreItem.objects.all():
        model = (row.model or "").strip() or None
        if model is not None:
            resources.append(
                Resource(
                    resource_id=f"generatore:{row.uid}",
                    resource_type=ResourceType.GENERATORE,
                    plant=_plant(row.plant),
                    model=model,
                )
            )

    return resources


def is_attivita_fixed(att: Attivita) -> bool:
    """Return whether an activity is in progress or completed (not re-plannable)."""
    return bool(att.actual_finish) or bool(att.actual_start)


def _planned_finish(att: Attivita) -> date | None:
    """Estimate planned finish when ``data_fine`` is missing."""
    if att.data_fine:
        return att.data_fine
    if att.data_inizio is None:
        return None
    days = _duration_days(att.duration_min, att.data_inizio, None)
    return WorkingCalendar(att.data_inizio).completion_date(0, days)


def fixed_calendar_window(att: Attivita) -> tuple[date, date] | None:
    """Return the locked calendar window for a fixed activity, if any."""
    if att.actual_finish:
        start = att.actual_start or att.data_inizio
        end = att.actual_finish
    elif att.actual_start:
        start = att.actual_start
        end = att.data_fine or _planned_finish(att)
    else:
        return None
    if start is None:
        return None
    if end is None:
        # In progress without a planned end: lock at least one working day.
        end = WorkingCalendar(start).completion_date(0, 1)
    if end < start:
        end = start
    return start, end


def is_gantt_task_fixed(
    task: dict[str, Any],
    att_by_pk: dict[int, Attivita],
    att_by_id: dict[str, Attivita],
) -> bool:
    """Return whether a Gantt task dict refers to a fixed (started/completed) activity."""
    orm_id = task.get("orm_id")
    if orm_id is not None:
        att = att_by_pk.get(int(orm_id))
        if att is not None:
            return is_attivita_fixed(att)
    activity_id = task.get("activity_id")
    if activity_id:
        att = att_by_id.get(activity_id)
        if att is not None:
            return is_attivita_fixed(att)
    return False


def filter_plannable_gantt_tasks(
    tasks: list[dict[str, Any]],
    attivita: Iterable[Attivita],
) -> list[dict[str, Any]]:
    """Drop started/completed activities from Gantt/table comparison payloads."""
    rows = list(attivita)
    by_pk = {a.pk: a for a in rows}
    by_id = {a.activity_id: a for a in rows}
    return [t for t in tasks if not is_gantt_task_fixed(t, by_pk, by_id)]


def fixed_activities_for_llm(
    attivita: Iterable[Attivita],
    plant_colors: dict[str, str],
) -> list[dict[str, Any]]:
    """Snapshot of locked activities for LLM context (not part of before/after diff)."""
    fixed_rows = [a for a in attivita if is_attivita_fixed(a)]
    if not fixed_rows:
        return []
    tasks = activities_to_gantt_tasks(fixed_rows, plant_colors)
    out: list[dict[str, Any]] = []
    for task, att in zip(tasks, fixed_rows, strict=True):
        window = fixed_calendar_window(att)
        resources = task.get("resources") or (
            ";".join(task.get("assigned_resources") or []) or None
        )
        entry: dict[str, Any] = {
            "activity_id": task["activity_id"],
            "job_id": task.get("job_id"),
            "commessa": task.get("commessa"),
            "item": task.get("item"),
            "attivita": task.get("activity"),
            "stato": "completata" if att.actual_finish else "in_corso",
            "inizio_effettivo": task.get("actual_start"),
            "fine_effettiva": task.get("actual_finish"),
            "inizio_pianificato_db": task.get("start"),
            "fine_pianificata_db": task.get("finish"),
            "risorse": resources,
            "nota": (
                "Intoccabile: esclusa dall'ottimizzazione; le risorse restano "
                "bloccate nell'intervallo effettivo."
            ),
        }
        if window:
            entry["finestra_blocco_risorse"] = {
                "inizio": window[0].isoformat(),
                "fine": window[1].isoformat(),
            }
        out.append(entry)
    return out


def solver_resource_ids(att: Attivita) -> list[str]:
    """Map ORM resource rows to solver resource ids (``tipo:uid``)."""
    ids: list[str] = []
    for row in att.risorse.all():
        tipo = (row.tipo or "").strip()
        uid = (row.risorsa_id or "").strip()
        if tipo and uid:
            resource_id = f"{tipo}:{uid}"
            if resource_id not in ids:
                ids.append(resource_id)
    return ids


def _fixed_resource_reservations(horizon_start: date) -> list[ResourceReservation]:
    """Block resources held by every started/completed activity.

    Started and completed activities are excluded from the optimization model
    entirely; this is the only trace they leave in the problem. The reserved
    window is clamped to the planning horizon.
    """
    from django.db.models import Q

    calendar = WorkingCalendar(horizon_start)
    rows = Attivita.objects.filter(
        Q(actual_start__isnull=False) | Q(actual_finish__isnull=False)
    ).prefetch_related("risorse")
    reservations: list[ResourceReservation] = []
    for att in rows:
        window = fixed_calendar_window(att)
        if window is None:
            continue
        start, end = window
        if end < horizon_start:
            continue
        resource_ids = solver_resource_ids(att)
        if not resource_ids:
            continue
        clamped_start = max(start, horizon_start)
        start_idx = calendar.date_to_working_index(clamped_start)
        duration = max(1, working_days_in_calendar_span(clamped_start, end))
        for resource_id in resource_ids:
            reservations.append(
                ResourceReservation(
                    resource_id=resource_id,
                    start_index=start_idx,
                    duration_days=duration,
                    activity_id=att.activity_id,
                )
            )
    return reservations


def _parse_predecessor_ids(att: Attivita, known_ids: set[str]) -> list[str]:
    """Parse the comma-separated ``predecessors`` field of an Attivita row.

    Only references to other activities of the same problem job are kept;
    unknown ids (e.g. activities of other commesse) are dropped.
    """
    raw = (att.predecessors or "").strip()
    if not raw:
        return []
    seen: list[str] = []
    for token in raw.split(","):
        pred_id = token.strip()
        if pred_id and pred_id != att.activity_id and pred_id in known_ids:
            if pred_id not in seen:
                seen.append(pred_id)
    return seen


def _to_opt_activity(
    att: Attivita,
    order: int,
    job_id: str,
    quantities_by_head: dict[str, dict[ResourceType, int]],
    earliest_start_overrides: dict[str, date],
    predecessors: list[str],
) -> Activity:
    raw_head = (att.modello_testa or "").strip() or None
    head_model = raw_head if raw_head in HEAD_MODELS else None
    quantities = quantities_by_head.get(raw_head or "", {}).copy()
    earliest = earliest_start_overrides.get(att.activity_id)

    return Activity(
        activity_id=att.activity_id,
        job_id=job_id,
        order=order,
        plant=_plant(att.stabilimento),
        duration_days=_duration_days(att.duration_min, att.data_inizio, att.data_fine),
        earliest_start=earliest,
        predecessors=predecessors,
        bore_min=_parse_float(att.bore_min),
        bore_mid=_parse_float(att.bore_mid),
        bore_max=_parse_float(att.bore_max),
        tube_pitch=_parse_float(att.tube_pitch),
        head_model=head_model,
        required_quantities=quantities,
    )


def activities_for_job_ids(job_ids: list[str]) -> list[Attivita]:
    """Return all activities belonging to the given canonical job ids."""
    from ui.services.identifiers import build_job_id

    unique_ids = list(dict.fromkeys(job_ids))
    if not unique_ids:
        return []
    activities = list(
        Attivita.objects.filter(_job_ids_query(unique_ids))
        .prefetch_related("risorse")
        .order_by("commessa", "item", "activity_id")
        .distinct()
    )

    stale: list[Attivita] = []
    for att in activities:
        expected_job_id = build_job_id(att.commessa)
        if att.job_id != expected_job_id:
            att.job_id = expected_job_id
            stale.append(att)
    if stale:
        Attivita.objects.bulk_update(stale, ["job_id"])

    return activities


def filter_job_ids_with_activities(job_ids: list[str]) -> list[str]:
    """Keep only job ids that resolve to at least one activity."""
    unique_ids = list(dict.fromkeys(job_ids))
    if not unique_ids:
        return []
    activities = activities_for_job_ids(unique_ids)
    return [
        jid
        for jid in unique_ids
        if any(_activity_matches_job(a, jid) for a in activities)
    ]


def build_scheduling_problem(
    job_ids: list[str],
    *,
    earliest_start_overrides: dict[str, date] | None = None,
    horizon_start: date | None = None,
) -> SchedulingProblem:
    """Build a SchedulingProblem from selected commesse (job ids).

    All activities of each selected job are included in full chains ordered
    by ``item``. Earliest-start constraints apply only when explicitly set in
    ``earliest_start_overrides`` (not inferred from ``data_inizio``).

    Args:
        job_ids: Canonical job_id strings (one per commessa).
        earliest_start_overrides: Optional per-activity earliest start dates.
        horizon_start: Planning horizon start; defaults to today or earliest override.

    Returns:
        SchedulingProblem: Ready for feasibility check and solving.

    Raises:
        OptimizationProblemError: When no jobs/activities match or inventory is empty.
    """
    from ui.services.optimization_explanation import ensure_django

    ensure_django()
    if not job_ids:
        raise OptimizationProblemError("Seleziona almeno una commessa.")

    overrides = earliest_start_overrides or {}
    unique_job_ids = list(dict.fromkeys(job_ids))

    attivita = activities_for_job_ids(unique_job_ids)
    if not attivita:
        raise OptimizationProblemError(
            "Nessuna attività trovata per le commesse selezionate."
        )

    missing_jobs = [
        jid
        for jid in unique_job_ids
        if not any(_activity_matches_job(a, jid) for a in attivita)
    ]
    if missing_jobs:
        raise OptimizationProblemError(
            f"Commesse senza attività: {', '.join(missing_jobs[:5])}"
            + ("…" if len(missing_jobs) > 5 else "")
        )

    compatibility = _fetch_generator_compatibility()
    if compatibility:
        configure_generator_compatibility(compatibility)

    resources = _fetch_inventory()
    if not resources:
        raise OptimizationProblemError("Inventario risorse vuoto.")

    quantities_by_head = _fetch_required_quantities()

    grouped: dict[str, list[Attivita]] = defaultdict(list)
    for att in attivita:
        grouped[att.commessa].append(att)

    commesse = sorted(grouped.keys())
    job_rows = {j.commessa: j for j in JobModel.objects.filter(commessa__in=commesse)}

    jobs: list[Job] = []
    for commessa in commesse:
        all_rows = sorted(grouped[commessa], key=lambda a: (a.item, a.activity_id))
        # Started/completed activities are NOT part of the model: they are
        # immutable facts. They surface only as resource reservations and as
        # earliest-start lower bounds on their successors.
        fixed_rows = [att for att in all_rows if is_attivita_fixed(att)]
        rows = [att for att in all_rows if not is_attivita_fixed(att)]
        if not rows:
            continue
        fixed_end_by_id: dict[str, date] = {}
        for att in fixed_rows:
            window = fixed_calendar_window(att)
            if window is not None:
                fixed_end_by_id[att.activity_id] = window[1]

        job_row = job_rows.get(commessa)
        job_id = rows[0].job_id or commessa
        # Precedences come from the Attivita.predecessors field; activities
        # without recorded predecessors are independent and may run in
        # parallel when enough compatible resources are available.
        known_ids = {att.activity_id for att in rows}
        opt_activities = []
        for order, att in enumerate(rows, 1):
            # A predecessor that already started/completed becomes a lower
            # bound: the successor starts after the locked window ends.
            pred_bound: date | None = None
            for raw_pred in (att.predecessors or "").split(","):
                end = fixed_end_by_id.get(raw_pred.strip())
                if end is not None:
                    candidate = end + timedelta(days=1)
                    if pred_bound is None or candidate > pred_bound:
                        pred_bound = candidate
            local_overrides = overrides
            if pred_bound is not None:
                existing = overrides.get(att.activity_id)
                local_overrides = {
                    **overrides,
                    att.activity_id: max(existing, pred_bound)
                    if existing
                    else pred_bound,
                }
            opt_activities.append(
                _to_opt_activity(
                    att,
                    order,
                    job_id,
                    quantities_by_head,
                    local_overrides,
                    _parse_predecessor_ids(att, known_ids),
                )
            )
        jobs.append(
            Job(
                job_id=job_id,
                due_date=_due_date(job_row, all_rows),
                penalty_per_week=_penalty_per_week(job_row),
                activities=opt_activities,
            )
        )

    if not jobs:
        raise OptimizationProblemError(
            "Tutte le attività selezionate sono già iniziate o completate: "
            "non c'è nulla da ottimizzare."
        )

    if horizon_start is None:
        override_dates = list(overrides.values())
        horizon_start = (
            min(override_dates) if override_dates else date.today() + timedelta(days=1)
        )

    reservations = _fixed_resource_reservations(horizon_start)

    return SchedulingProblem(
        jobs=jobs,
        resources=resources,
        horizon_start=horizon_start,
        horizon_days=None,
        resource_reservations=reservations,
    )


def problem_summary(problem: SchedulingProblem) -> dict[str, Any]:
    """Return a JSON-serialisable summary of a SchedulingProblem."""
    activities = sum(len(j.activities) for j in problem.jobs)
    by_type: dict[str, int] = defaultdict(int)
    for resource in problem.resources:
        by_type[resource.resource_type.value] += 1
    return {
        "jobs": len(problem.jobs),
        "activities": activities,
        "resource_reservations": len(problem.resource_reservations),
        "resources": len(problem.resources),
        "resources_by_type": dict(by_type),
        "horizon_start": problem.horizon_start.isoformat(),
        "horizon_days": problem.horizon_days,
    }


def _strip_resource_prefix(resource_id: str) -> str:
    if ":" in resource_id:
        return resource_id.split(":", 1)[1]
    return resource_id


def _resources_by_attivita_pk(att_pks: list[int]) -> dict[int, list[str]]:
    """Bulk-load resource codes keyed by Attivita primary key."""
    if not att_pks:
        return {}
    grouped: dict[int, list[str]] = defaultdict(list)
    for att_id, rid in RisorsaAttivita.objects.filter(
        attivita_id__in=att_pks
    ).values_list("attivita_id", "risorsa_id"):
        if rid:
            grouped[att_id].append(_strip_resource_prefix(rid))
    return {pk: sorted(dict.fromkeys(codes)) for pk, codes in grouped.items()}


def hydrate_gantt_tasks_resources(tasks: list[dict]) -> None:
    """Fill ``assigned_resources`` on task dicts from ``RisorsaAttivita`` rows."""
    if not tasks:
        return

    pks: set[int] = set()
    need_activity_id: list[dict] = []
    for task in tasks:
        orm_id = task.get("orm_id")
        if orm_id:
            pks.add(int(orm_id))
        elif task.get("activity_id"):
            need_activity_id.append(task)

    if need_activity_id:
        aids = [t["activity_id"] for t in need_activity_id]
        pk_by_aid = dict(
            Attivita.objects.filter(activity_id__in=aids).values_list(
                "activity_id", "pk"
            )
        )
        for task in need_activity_id:
            pk = pk_by_aid.get(task["activity_id"])
            if pk:
                task["orm_id"] = pk
                pks.add(pk)

    resources_map = _resources_by_attivita_pk(list(pks))
    for task in tasks:
        pk = task.get("orm_id")
        if not pk:
            continue
        codes = resources_map.get(int(pk), [])
        task["assigned_resources"] = codes
        task["resources"] = ";".join(codes) if codes else None


def attivita_to_gantt_task(att: Attivita, plant_colors: dict[str, str]) -> dict:
    """Convert an Attivita ORM row to a Gantt-compatible task dict."""
    plant = att.stabilimento or None
    return {
        "commessa": att.commessa,
        "item": str(att.item).zfill(2),
        "job_id": att.job_id,
        "activity_id": att.activity_id,
        "activity": att.activity or "",
        "duration_min": att.duration_min,
        "start": att.data_inizio.isoformat() if att.data_inizio else None,
        "finish": att.data_fine.isoformat() if att.data_fine else None,
        "actual_start": att.actual_start.isoformat() if att.actual_start else None,
        "actual_finish": att.actual_finish.isoformat() if att.actual_finish else None,
        "baseline_start": att.baseline_start.isoformat()
        if att.baseline_start
        else None,
        "baseline_finish": att.baseline_finish.isoformat()
        if att.baseline_finish
        else None,
        "plant": plant,
        "plant_color": plant_colors.get(plant or "", "#4a90e2"),
        "responsible": att.responsabile or None,
        "resources": None,
        "assigned_resources": [],
        "predecessors": att.predecessors,
        "orm_id": att.pk,
    }


def activities_to_gantt_tasks(
    attivita_qs,
    plant_colors: dict[str, str],
) -> list[dict]:
    """Convert a queryset or list of Attivita to Gantt task dicts."""
    tasks = [attivita_to_gantt_task(a, plant_colors) for a in attivita_qs]
    hydrate_gantt_tasks_resources(tasks)
    return tasks


def schedule_result_to_gantt_tasks(
    result: ScheduleResult,
    attivita_by_id: dict[str, Attivita],
    plant_colors: dict[str, str],
    *,
    attivita: Iterable[Attivita] | None = None,
) -> list[dict]:
    """Convert a ScheduleResult into Gantt task dicts using solved dates."""
    attivita_rows = (
        list(attivita) if attivita is not None else list(attivita_by_id.values())
    )
    tasks: list[dict] = []
    seen_pks: set[int] = set()
    for sched in result.activities:
        att = attivita_by_id.get(sched.activity_id)
        if att is None:
            att = match_attivita_for_solver_activity_id(
                sched.activity_id, attivita_rows
            )
        if att is None:
            continue
        seen_pks.add(att.pk)
        task = attivita_to_gantt_task(att, plant_colors)
        task["start"] = sched.start_date.isoformat()
        task["finish"] = sched.end_date.isoformat()
        task["assigned_resource_ids"] = list(sched.assigned_resource_ids)
        task["assigned_resources"] = [
            _strip_resource_prefix(rid) for rid in sched.assigned_resource_ids
        ]
        task["resources"] = (
            ";".join(task["assigned_resources"]) if task["assigned_resources"] else None
        )
        tasks.append(task)

    return tasks


def schedule_metrics_from_tasks(
    tasks: list[dict],
    job_ids: list[str],
) -> dict[str, Any]:
    """Estimate penalty and lateness from Gantt task dicts (baseline or solved)."""
    jobs = {j.job_id: j for j in JobModel.objects.filter(job_id__in=job_ids)}
    cal = WorkingCalendar(date.today())
    completion_by_job: dict[str, date] = {}
    for task in tasks:
        jid = task.get("job_id")
        finish_raw = task.get("finish")
        if not jid or not finish_raw:
            continue
        try:
            finish = date.fromisoformat(str(finish_raw)[:10])
        except ValueError:
            continue
        prev = completion_by_job.get(jid)
        completion_by_job[jid] = finish if prev is None else max(prev, finish)

    total_penalty = 0.0
    num_late_jobs = 0
    num_late_activities = 0

    for jid in job_ids:
        job = jobs.get(jid)
        if job is None:
            continue
        due = _due_date(job, [])
        completion = completion_by_job.get(jid)
        if completion is not None:
            delay_days = cal.calendar_delay_days(completion, due)
            if delay_days > 0:
                num_late_jobs += 1
                delay_weeks = math.ceil(delay_days / 7)
                total_penalty += _penalty_per_week(job) * delay_weeks

        for task in tasks:
            if task.get("job_id") != jid:
                continue
            finish_raw = task.get("finish")
            if not finish_raw:
                continue
            try:
                finish = date.fromisoformat(str(finish_raw)[:10])
            except ValueError:
                continue
            if cal.calendar_delay_days(finish, due) > 0:
                num_late_activities += 1

    return {
        "total_penalty": total_penalty,
        "num_late_jobs": num_late_jobs,
        "num_late_activities": num_late_activities,
        "n_activities": len(tasks),
    }


def deadlines_for_jobs(job_ids: list[str]) -> dict[str, str]:
    """Return delivery_date strings keyed by job_id for Gantt deadline markers."""
    rows = JobModel.objects.filter(job_id__in=job_ids).values("job_id", "delivery_date")
    out: dict[str, str] = {}
    for r in rows:
        if not r["delivery_date"]:
            continue
        raw = r["delivery_date"]
        out[r["job_id"]] = (
            raw.isoformat() if hasattr(raw, "isoformat") else str(raw)[:10]
        )
    return out
