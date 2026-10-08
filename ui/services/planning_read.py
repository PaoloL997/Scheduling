"""Read layer for the planning pages, the Gantt and the tablet bridge.

Everything in this module is a pure read: it loads activities, commesse,
assignments and problems with a handful of queries and turns them into the
dicts the templates and the JSON APIs render.  Nothing here writes to the
database, so the same call can be repeated freely and two concurrent requests
can never race each other.

The status rules live here and only here.  The planning page, the Gantt and
the tablet receive ``status_pills``, ``state_key`` and ``delay_days`` already
computed and just display them.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from types import SimpleNamespace

from django.db.models import Count, F
from django.utils.timezone import localdate

from ui.models import Attivita, Job, ProblemaAttivita, Stabilimento
from ui.services.parsing import parse_iso_day  # noqa: F401  (re-exported)
from ui.services.resource_types import DEFAULT_PLANT_COLOR
from ui.services.activity_inventory import (
    get_testa_min_requirements,
    load_testa_configs,
)
from ui.services.problem_detection import effective_finish, missing_minimum_resources
from ui.services.resource_display import (
    format_activity_resources,
    format_activity_resources_html,
)

_JOB_STATUS_LABELS = {
    "rischio_penale": "RISCHIO PENALE",
    "in_ritardo": "IN RITARDO",
    "in_tempo": "IN TEMPO",
    "pianificata": "PIANIFICATA",
    "non_pianificata": "NON PIANIFICATA",
}


# -- date helpers --------------------------------------------------------------


# -- activity status -----------------------------------------------------------


def compute_activity_status(
    data_inizio: date | None,
    data_fine: date | None,
    actual_start: date | None,
    actual_finish: date | None,
    today: date,
) -> list[dict]:
    """Return the ordered status pills of one activity.

    An activity may carry two pills at once (``IN RITARDO`` plus
    ``IN LAVORAZIONE``).  Without planned dates it is unscheduled.

    Args:
        data_inizio: Planned start, or ``None``.
        data_fine: Planned finish, or ``None``.
        actual_start: Recorded start, or ``None``.
        actual_finish: Recorded finish, or ``None``.
        today: Reference day for every "now" comparison.

    Returns:
        list[dict]: ``{"key", "label"}`` dicts, never empty.  ``key`` is the
            stable CSS modifier, ``label`` the Italian text.
    """
    if data_inizio is None or data_fine is None:
        return [{"key": "non_pianificata", "label": "NON PIANIFICATA"}]

    is_completed = bool(actual_start and actual_finish)
    is_started = bool(actual_start and not actual_finish)
    is_late = bool(actual_start and actual_start > data_inizio)
    # Not started once the planned start day is over: on the start day itself
    # the operator still has the whole day to record the start.
    is_overdue = not actual_start and today > data_inizio
    # Still open past the planned finish: late even when it started on time.
    is_past_due = not actual_finish and today > data_fine
    # Never started and past the planned finish: the plan is stale and the
    # activity still holds its resources.
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


def compute_delay_days(
    data_inizio: date | None,
    data_fine: date | None,
    actual_start: date | None,
    actual_finish: date | None,
    today: date,
) -> int:
    """Return how many calendar days the activity is behind plan.

    Zero exactly when :func:`compute_activity_status` carries no
    ``in_ritardo`` pill.  Otherwise the largest of: the days lost by a late
    start, the days elapsed since the planned start without starting, and
    the days still open past the planned finish.
    """
    if actual_finish and actual_start:
        return 0
    if data_inizio is None or data_fine is None:
        return 0

    started_late = bool(actual_start and actual_start > data_inizio)
    overdue = not actual_start and today > data_inizio
    past_due = not actual_finish and today > data_fine
    if not (started_late or overdue or past_due):
        return 0

    delay = 0
    if started_late:
        delay = max(delay, (actual_start - data_inizio).days)
    if overdue:
        delay = max(delay, (today - data_inizio).days)
    if past_due:
        delay = max(delay, (today - data_fine).days)
    return max(delay, 1)


def activity_state_key(pills: list[dict]) -> str:
    """Collapse the pills into the single state the commessa counters use."""
    keys = [p["key"] for p in pills or []]
    if "in_ritardo" in keys:
        return "in_ritardo"
    if "in_lavorazione" in keys:
        return "in_lavorazione"
    if "completata" in keys:
        return "completata"
    if "pianificata" in keys:
        return "pianificata"
    return "non_pianificata"


# -- commessa aggregates -------------------------------------------------------


def projected_end(task: dict) -> date | None:
    """Return the day the activity is expected to end, as things stand.

    ``actual_finish`` when done, the working-day ``effective_finish`` while
    running, the planned finish while waiting to start.  ``None`` without
    planned dates.
    """
    act_f = parse_iso_day(task.get("actual_finish"))
    if act_f is not None:
        return act_f
    plan_s = parse_iso_day(task.get("start"))
    plan_f = parse_iso_day(task.get("finish"))
    if plan_s is None or plan_f is None:
        return None
    act_s = parse_iso_day(task.get("actual_start"))
    if act_s is None:
        return plan_f
    eff = parse_iso_day(task.get("effective_finish"))
    if eff is None:
        eff = effective_finish(
            SimpleNamespace(data_inizio=plan_s, data_fine=plan_f, actual_start=act_s)
        )
    return max(plan_f, eff) if eff else plan_f


def aggregate_job_status(
    activities: list[dict],
    delivery_date: str | date | None = None,
) -> str:
    """Return the aggregate commessa status from its task dicts.

    Priority: ``rischio_penale`` → ``in_ritardo`` → ``in_tempo`` →
    ``pianificata``; ``non_pianificata`` when there are no activities.

    ``rischio_penale`` means the commessa is expected to end after its
    delivery date as things stand: a planned finish past the deadline counts
    even when nothing has started, since the plan itself breaks the
    deadline.
    """
    if not activities:
        return "non_pianificata"

    deadline = parse_iso_day(delivery_date)
    late = 0
    started = 0
    penalty_risk = False

    for task in activities:
        state = task.get("state_key") or activity_state_key(
            task.get("status_pills") or []
        )
        if state == "in_ritardo":
            late += 1
        if task.get("actual_start"):
            started += 1

        if deadline is None:
            continue
        end = projected_end(task)
        if end is not None and end > deadline:
            penalty_risk = True

    if penalty_risk:
        return "rischio_penale"
    if late > 0:
        return "in_ritardo"
    if started > 0:
        return "in_tempo"
    return "pianificata"


def is_job_completed(activities: list[dict]) -> bool:
    """True when every activity carries both actual dates."""
    if not activities:
        return False
    return all(a.get("actual_start") and a.get("actual_finish") for a in activities)


def has_plannable_activities(activities: list[dict]) -> bool:
    """True when at least one activity is neither started nor completed."""
    return any(
        not (a.get("actual_start") or a.get("actual_finish")) for a in activities
    )


def count_job_open_problems(activities: list[dict]) -> int:
    """Sum the open problems of a commessa's activities."""
    return sum(a.get("warning_count") or 0 for a in activities)


def earliest_activity_start(activities: list[dict]) -> date | None:
    """Return the earliest planned (or actual) start among the task dicts."""
    earliest: date | None = None
    for task in activities or []:
        day = parse_iso_day(task.get("start")) or parse_iso_day(
            task.get("actual_start")
        )
        if day is None:
            continue
        if earliest is None or day < earliest:
            earliest = day
    return earliest


# -- task dicts ----------------------------------------------------------------


def attivita_to_task_dict(
    a: Attivita,
    *,
    today: date | None = None,
    configs: dict[str, object] | None = None,
) -> dict:
    """Convert one :class:`~ui.models.Attivita` into the planning row dict.

    Args:
        a: Activity with ``risorse`` and ``problemi`` prefetched.
        today: Reference day; defaults to the local date.
        configs: Preloaded testa configurations from
            :func:`~ui.services.activity_inventory.load_testa_configs`.
            Pass them when converting many activities, otherwise each row
            costs one query.

    Returns:
        dict: The row, including ``status_pills`` / ``state_key`` /
            ``delay_days`` (computed once, here), ``warning_count`` and
            ``note_count``, and ``min_requirements`` / ``can_start`` for the
            start guard.
    """
    reference = today or localdate()

    risorse_all = list(a.risorse.all())
    res_fmt = format_activity_resources(risorse_all)
    resources = ";".join(res_fmt["flat_ids"])
    problemi_all = list(a.problemi.all())
    warning_count = sum(1 for p in problemi_all if not p.risolto)
    note_count = sum(1 for p in problemi_all if p.risolto)
    # Resources with an open «spostamento» note: they must still be moved
    # from another plant, so the kanban and the Gantt flag their chips.
    moving_resources = sorted(
        {
            p.risorsa_uid
            for p in problemi_all
            if not p.risolto
            and p.tipo == ProblemaAttivita.TIPO_SPOSTAMENTO
            and p.risorsa_uid
        }
    )

    status_pills = compute_activity_status(
        a.data_inizio, a.data_fine, a.actual_start, a.actual_finish, reference
    )
    delay_days = compute_delay_days(
        a.data_inizio, a.data_fine, a.actual_start, a.actual_finish, reference
    )
    expected_finish = effective_finish(a)

    tp60 = bool(a.tp60_custom)
    min_requirements = get_testa_min_requirements(
        a.modello_testa or None, tp60, configs=configs
    )
    missing_minimum = missing_minimum_resources(
        a, [(r.tipo, r.risorsa_id) for r in risorse_all], configs=configs
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
        "effective_finish": expected_finish.isoformat() if expected_finish else None,
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
        "moving_resources": moving_resources,
        "modello_testa": a.modello_testa or None,
        "bore_min": float(a.bore_min) if a.bore_min is not None else None,
        "bore_max": float(a.bore_max) if a.bore_max is not None else None,
        "bore_mid": float(a.bore_mid) if a.bore_mid is not None else None,
        "tube_pitch": float(a.tube_pitch) if a.tube_pitch is not None else None,
        "tp60_custom": tp60,
        "predecessors": a.predecessors_text() or None,
        "predecessor_names": ", ".join(
            p.activity or p.activity_id for p in a.predecessori.all()
        )
        or None,
        "source": "attivita",
        "orm_id": a.pk,
        "warning_count": warning_count,
        "note_count": note_count,
        "status_pills": status_pills,
        "status_pills_json": json.dumps(status_pills),
        "state_key": activity_state_key(status_pills),
        "delay_days": delay_days,
        "min_requirements": min_requirements,
        "min_requirements_json": json.dumps(min_requirements),
        "missing_minimum": missing_minimum,
        "can_start": not missing_minimum,
    }


def load_task_dict(pk: int) -> dict | None:
    """Return the row dict of one activity, or ``None`` when it does not exist."""
    try:
        att = Attivita.objects.prefetch_related(
            "risorse", "problemi", "predecessori"
        ).get(pk=pk)
    except Attivita.DoesNotExist:
        return None
    return attivita_to_task_dict(att)


# -- page-level reads ----------------------------------------------------------


def fetch_tasks(
    stabilimento: str | None = None,
    *,
    commessa: str | None = None,
) -> list[dict]:
    """Return activities as planning row dicts.

    Four queries: activities, assignments, problems, predecessors; plus the
    testa configurations.  Rows are ordered by commessa, planned start
    (missing first) and activity id.

    Args:
        stabilimento: Optional plant filter (case-insensitive).
        commessa: Optional exact commessa filter.
    """
    qs = Attivita.objects.prefetch_related(
        "risorse", "problemi", "predecessori"
    ).order_by("job_id", F("data_inizio").asc(nulls_first=True), "activity_id")
    if stabilimento and stabilimento.strip():
        qs = qs.filter(stabilimento__iexact=stabilimento.strip())
    if commessa is not None:
        qs = qs.filter(commessa=commessa)
    configs = load_testa_configs()
    today = localdate()
    return [attivita_to_task_dict(a, today=today, configs=configs) for a in qs]


def fetch_jobs() -> list[dict]:
    """Return every commessa with its activity counts.

    Commesse that exist only in the activity table (no :class:`Job` row) are
    included with blank metadata.
    """
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
    for commessa in sorted(set(jobs_by_commessa) | set(act_counts)):
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


def _job_aggregates(job: dict, activities: list[dict]) -> dict:
    """Return *job* extended with the fields every client displays as-is."""
    status = aggregate_job_status(activities, job.get("delivery_date"))
    return {
        **job,
        "n_open_problems": count_job_open_problems(activities),
        "status": status,
        "status_label": _JOB_STATUS_LABELS[status],
        "completed": is_job_completed(activities),
    }


_EMPTY_JOB = {
    "job_id": "",
    "total_cost": None,
    "penalty": None,
    "delivery_date": None,
    "n_activities": 0,
    "n_items": 0,
}


def job_summary(commessa: str) -> dict:
    """Return the header fields of one commessa after a write.

    The planning page swaps these into the commessa card, so the aggregate
    status is never recomputed client-side.
    """
    activities = fetch_tasks(commessa=commessa)
    job = (
        Job.objects.filter(commessa=commessa)
        .values("job_id", "commessa", "total_cost", "penalty", "delivery_date")
        .first()
    ) or {**_EMPTY_JOB, "commessa": commessa}
    job["n_activities"] = len(activities)
    job["n_items"] = len({a["item"] for a in activities})
    return _job_aggregates(job, activities)


def fetch_gantt() -> dict:
    """Return the ``/api/gantt/`` payload: tasks, deadlines and job statuses.

    Tasks are the planning row dicts plus ``plant_color``; ``jobs`` maps each
    ``job_id`` to its aggregate status so the Gantt labels and the kanban show
    the same state as the planning page.
    """
    plant_colors = {s.nome: s.colore for s in Stabilimento.objects.all()}
    tasks = fetch_tasks()
    tasks.sort(key=lambda t: (t["commessa"], t["item"], t["activity"] or ""))
    for task in tasks:
        task["plant_color"] = plant_colors.get(task["plant"] or "", DEFAULT_PLANT_COLOR)

    by_commessa: dict[str, list[dict]] = defaultdict(list)
    for task in tasks:
        by_commessa[task["commessa"]].append(task)

    deadlines: dict[str, str] = {}
    jobs: dict[str, dict] = {}
    for job in fetch_jobs():
        if not job.get("job_id"):
            continue
        if job.get("delivery_date"):
            deadlines[job["job_id"]] = job["delivery_date"]
        agg = _job_aggregates(job, by_commessa.get(job["commessa"], []))
        jobs[job["job_id"]] = {
            "commessa": job["commessa"],
            "status": agg["status"],
            "status_label": agg["status_label"],
            "completed": agg["completed"],
            "n_open_problems": agg["n_open_problems"],
        }
    return {"tasks": tasks, "deadlines": deadlines, "jobs": jobs}


def fetch_grouped() -> list[dict]:
    """Return activities grouped by commessa, each paired with its job row.

    Each entry is ``{"job": {...}, "activities": [task dicts]}``.  The job
    dict carries the aggregate ``status`` / ``status_label``, ``completed``
    and ``n_open_problems`` so no client recomputes them.  Activities are
    sorted by planned start within the group; groups by earliest start, then
    commessa name.
    """
    tasks = fetch_tasks()
    jobs = fetch_jobs()

    job_by_commessa = {j["commessa"]: j for j in jobs}

    groups_map: dict[str, list] = defaultdict(list)
    for task in tasks:
        groups_map[task["commessa"]].append(task)

    result = []
    for commessa in sorted(set(groups_map) | set(job_by_commessa)):
        job = job_by_commessa.get(commessa, {**_EMPTY_JOB, "commessa": commessa})
        activities = groups_map.get(commessa, [])
        activities.sort(
            key=lambda t: (
                parse_iso_day(t.get("start"))
                or parse_iso_day(t.get("actual_start"))
                or date.max,
                str(t.get("activity_id") or ""),
            )
        )
        result.append(
            {"job": _job_aggregates(job, activities), "activities": activities}
        )
    result.sort(
        key=lambda g: (
            earliest_activity_start(g["activities"]) or date.max,
            (g["job"].get("commessa") or "").lower(),
        )
    )
    return result
