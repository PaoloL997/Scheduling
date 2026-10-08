"""Optimization results as session payloads and as a compact diff for the LLM.

Two things live here:

* :func:`current_plan_payload` and :func:`build_result_payload_from_schedule`
  turn the database plan and a solved ``ScheduleResult`` into the
  ``result_payload`` the optimization wizard keeps in the session and shows
  on the results page;
* :func:`format_optimization_result_for_llm` turns that payload into the
  before/after diff the explanation agent reads, and
  :func:`generate_optimization_explanation` sends it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date
from typing import Any

from src.optimization.calendar import WorkingCalendar
from src.optimization.diagnostics import JobDiagnosis
from src.optimization.output_model import ScheduleResult
from ui.services.parsing import parse_iso_day

_CAUSA_LABELS: dict[str, str] = {
    "resource_contention": "contesa_risorse",
    "earliest_start": "vincolo_inizio_minimo",
    "chain_too_long": "catena_troppo_lunga",
}

_DEFAULT_SETTINGS_MODULE = "config.settings"


def ensure_django(*, settings_module: str = _DEFAULT_SETTINGS_MODULE) -> None:
    """Initialize the Django ORM from a notebook or a script.

    Call once before building a problem or a payload when running outside
    ``manage.py``.  The application never needs it.
    """
    import os

    import django
    from django.conf import settings

    if settings.configured:
        return
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)
    django.setup()


# -- session payloads ----------------------------------------------------------


def _plant_colors() -> dict[str, str]:
    from ui.models import Stabilimento

    return {s.nome: s.colore for s in Stabilimento.objects.all()}


def current_plan_payload(
    job_ids: list[str], *, plant_colors: dict[str, str] | None = None
) -> dict[str, Any]:
    """Return the current plan of *job_ids* as the wizard's "before" side.

    Keys: ``tasks_before`` (plannable activities as Gantt tasks),
    ``fixed_activities`` (started/completed ones, for the LLM context),
    ``deadlines`` and ``job_ids``; plus ``_attivita`` and ``_tasks_before_all``
    for :func:`build_result_payload_from_schedule`, which strips them.
    """
    from ui.services.optimization_problem import (
        activities_for_job_ids,
        activities_to_gantt_tasks,
        deadlines_for_jobs,
        filter_plannable_gantt_tasks,
        fixed_activities_for_llm,
    )

    unique_job_ids = list(dict.fromkeys(job_ids))
    colors = plant_colors if plant_colors is not None else _plant_colors()
    attivita = activities_for_job_ids(unique_job_ids)
    tasks_before_all = activities_to_gantt_tasks(attivita, colors)
    return {
        "tasks_before": filter_plannable_gantt_tasks(tasks_before_all, attivita),
        "fixed_activities": fixed_activities_for_llm(attivita, colors),
        "deadlines": deadlines_for_jobs(unique_job_ids),
        "job_ids": unique_job_ids,
        "_attivita": attivita,
        "_tasks_before_all": tasks_before_all,
        "_colors": colors,
    }


def build_result_payload_from_schedule(
    job_ids: list[str],
    result: ScheduleResult,
    diagnoses: Iterable[JobDiagnosis],
    *,
    plant_colors: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return the session ``result_payload`` for a solved schedule.

    Args:
        job_ids: Commesse of the problem (``[j.job_id for j in problem.jobs]``).
        result: Feasible output of :func:`src.optimization.solver.schedule`.
        diagnoses: The diagnoses returned with it.
        plant_colors: Plant → colour for the Gantt tasks; read from the
            database when omitted.

    Raises:
        ValueError: When *job_ids* name no activity.
    """
    from ui.services.optimization_problem import (
        activities_to_gantt_tasks,
        filter_plannable_gantt_tasks,
        is_attivita_fixed,
        schedule_metrics_from_tasks,
        schedule_result_to_gantt_tasks,
    )

    before = current_plan_payload(job_ids, plant_colors=plant_colors)
    attivita = before.pop("_attivita")
    tasks_before_all = before.pop("_tasks_before_all")
    colors = before.pop("_colors")
    unique_job_ids = before["job_ids"]
    if not attivita:
        raise ValueError(
            f"Nessuna attività nel database per job_ids={unique_job_ids!r}."
        )

    attivita_by_id = {a.activity_id: a for a in attivita}
    tasks_after = filter_plannable_gantt_tasks(
        schedule_result_to_gantt_tasks(
            result, attivita_by_id, colors, attivita=attivita
        ),
        attivita,
    )
    fixed_tasks = activities_to_gantt_tasks(
        [a for a in attivita if is_attivita_fixed(a)], colors
    )
    metrics_after = schedule_metrics_from_tasks(
        tasks_after + fixed_tasks, unique_job_ids
    )

    return {
        **before,
        "tasks_after": tasks_after,
        "metrics_before": schedule_metrics_from_tasks(tasks_before_all, unique_job_ids),
        "metrics_after": {
            "total_penalty": result.total_penalty,
            "num_late_jobs": result.num_late_jobs,
            "num_late_activities": metrics_after["num_late_activities"],
            "n_activities": metrics_after["n_activities"],
        },
        "result": {
            "total_penalty": result.total_penalty,
            "num_late_jobs": result.num_late_jobs,
            "jobs": [
                {
                    "job_id": jr.job_id,
                    "due_date": jr.due_date.isoformat(),
                    "completion_date": jr.completion_date.isoformat(),
                    "delay_days": jr.delay_days,
                    "delay_weeks": jr.delay_weeks,
                    "penalty": jr.penalty,
                }
                for jr in result.jobs
            ],
        },
        "diagnoses": [
            {
                "job_id": d.job_id,
                "late": d.late,
                "delay_days": d.delay_days,
                "primary_cause": d.primary_cause,
                "detail": d.detail,
            }
            for d in diagnoses
        ],
    }


# -- the diff the LLM reads ------------------------------------------------------


def _iso_day(value: Any) -> str | None:
    if not value:
        return None
    return str(value)[:10]


def _strip_resource_code(resource_id: str) -> str:
    text = str(resource_id or "")
    sep = text.find(":")
    return text[sep + 1 :] if sep >= 0 else text


def _task_resources(task: dict[str, Any]) -> list[str]:
    assigned = task.get("assigned_resources") or []
    if assigned:
        codes = [_strip_resource_code(r) for r in assigned]
    elif task.get("resources"):
        codes = [
            _strip_resource_code(part)
            for part in str(task["resources"]).split(";")
            if part.strip()
        ]
    else:
        codes = []
    return list(dict.fromkeys(code for code in codes if code))


def _day_delta(before: str | None, after: str | None) -> int | None:
    start = parse_iso_day(before)
    end = parse_iso_day(after)
    if start is None or end is None:
        return None
    return (end - start).days


def _is_late_finish(finish: Any, job_id: str | None, deadlines: dict[str, str]) -> bool:
    finish_day = _iso_day(finish)
    if not finish_day or not job_id:
        return False
    deadline = deadlines.get(job_id) or deadlines.get(str(job_id))
    if not deadline:
        return False
    return finish_day > str(deadline)[:10]


def _job_completion_dates(tasks: list[dict[str, Any]]) -> dict[str, str]:
    completion: dict[str, date] = {}
    for task in tasks:
        job_id = task.get("job_id")
        finish = parse_iso_day(task.get("finish"))
        if not job_id or finish is None:
            continue
        prev = completion.get(job_id)
        completion[job_id] = finish if prev is None else max(prev, finish)
    return {jid: finish.isoformat() for jid, finish in completion.items()}


def _job_state_before(
    completion_date: str | None,
    deadline: str | None,
    calendar: WorkingCalendar,
) -> dict[str, Any]:
    completion = parse_iso_day(completion_date)
    due = parse_iso_day(deadline)
    if completion is None or due is None:
        return {
            "fine_commessa": completion_date,
            "in_ritardo": False,
            "ritardo_giorni": 0,
        }
    delay_days = calendar.calendar_delay_days(completion, due)
    return {
        "fine_commessa": completion.isoformat(),
        "in_ritardo": delay_days > 0,
        "ritardo_giorni": delay_days,
    }


def _activity_snapshot(
    task: dict[str, Any], deadlines: dict[str, str]
) -> dict[str, Any]:
    resources = _task_resources(task)
    finish = _iso_day(task.get("finish"))
    return {
        "inizio": _iso_day(task.get("start")),
        "fine": finish,
        "risorse": resources,
        "risorse_joined": ";".join(resources) if resources else None,
        "oltre_deadline": _is_late_finish(finish, task.get("job_id"), deadlines),
    }


def _activity_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_resources = before["risorse"]
    after_resources = after["risorse"]
    return {
        "inizio_giorni": _day_delta(before["inizio"], after["inizio"]),
        "fine_giorni": _day_delta(before["fine"], after["fine"]),
        "risorse_cambiate": before_resources != after_resources,
        "risorse_aggiunte": [r for r in after_resources if r not in before_resources],
        "risorse_rimosse": [r for r in before_resources if r not in after_resources],
    }


def _diagnosis_entry(diagnosis: dict[str, Any] | None) -> dict[str, Any] | None:
    if not diagnosis:
        return None
    cause = diagnosis.get("primary_cause")
    return {
        "in_ritardo": bool(diagnosis.get("late")),
        "ritardo_giorni": diagnosis.get("delay_days", 0),
        "causa_principale": _CAUSA_LABELS.get(cause, cause),
        "causa_codice": cause,
        "dettaglio": diagnosis.get("detail"),
    }


def _collect_activity_changes(
    tasks_before: list[dict[str, Any]],
    after_by_id: dict[str, dict[str, Any]],
    deadlines: dict[str, str],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for before_task in tasks_before:
        activity_id = before_task.get("activity_id")
        after_task = after_by_id.get(activity_id) if activity_id else None
        if after_task is None:
            continue

        before_snap = _activity_snapshot(before_task, deadlines)
        after_snap = _activity_snapshot(after_task, deadlines)
        if all(before_snap[k] == after_snap[k] for k in ("inizio", "fine", "risorse")):
            continue

        changes.append(
            {
                "activity_id": activity_id,
                "job_id": before_task.get("job_id"),
                "commessa": before_task.get("commessa"),
                "item": before_task.get("item"),
                "attivita": before_task.get("activity"),
                "stabilimento": before_task.get("plant"),
                "attuale": before_snap,
                "proposta": after_snap,
                "delta": _activity_delta(before_snap, after_snap),
            }
        )

    changes.sort(
        key=lambda row: (
            -(abs(row["delta"]["fine_giorni"] or 0)),
            -(abs(row["delta"]["inizio_giorni"] or 0)),
            row.get("commessa") or "",
            str(row.get("item") or ""),
        )
    )
    return changes


def _job_change_summary(
    job_id: str, activity_changes: list[dict[str, Any]]
) -> dict[str, Any]:
    job_changes = [row for row in activity_changes if row.get("job_id") == job_id]
    fine_deltas = [
        row["delta"]["fine_giorni"]
        for row in job_changes
        if row["delta"]["fine_giorni"] is not None
    ]
    start_deltas = [
        row["delta"]["inizio_giorni"]
        for row in job_changes
        if row["delta"]["inizio_giorni"] is not None
    ]
    return {
        "job_id": job_id,
        "attivita_modificate": len(job_changes),
        "attivita_spostate_avanti": sum(1 for d in fine_deltas if d < 0),
        "attivita_spostate_indietro": sum(1 for d in fine_deltas if d > 0),
        "max_anticipo_giorni": abs(min(fine_deltas))
        if fine_deltas and min(fine_deltas) < 0
        else 0,
        "max_posticipo_giorni": max(fine_deltas) if fine_deltas else 0,
        "max_shift_inizio_giorni": max((abs(d) for d in start_deltas), default=0),
        "risorse_riassegnate": sum(
            1 for row in job_changes if row["delta"]["risorse_cambiate"]
        ),
    }


def format_optimization_result_for_llm(
    payload: dict[str, Any],
    *,
    max_attivita_modificate: int | None = None,
) -> dict[str, Any]:
    """Build the compact before/after diff of a ``result_payload``.

    Args:
        payload: A ``result_payload`` from
            :func:`build_result_payload_from_schedule` (``tasks_before``,
            ``tasks_after``, ``deadlines``, metrics, ``result``,
            ``diagnoses``, ``job_ids``).
        max_attivita_modificate: When set, keep only that many changed
            activities, the ones with the largest shift of the finish first.

    Returns:
        The structured summary for the LLM.  On an incomplete payload
        ``meta.ok`` is False and ``meta.errore`` says why.
    """
    tasks_before = payload.get("tasks_before") or []
    tasks_after = payload.get("tasks_after") or []
    fixed_activities = payload.get("fixed_activities") or []
    if not tasks_before or not tasks_after:
        return {
            "meta": {
                "ok": False,
                "lingua": "it",
                "errore": "Risultato incompleto: mancano tasks_before o tasks_after.",
                "suggerimento": (
                    "Serve il result_payload di build_result_payload_from_schedule "
                    f"(chiavi ricevute: {sorted(payload.keys())})."
                ),
            }
        }

    deadlines = payload.get("deadlines") or {}
    metrics_before = payload.get("metrics_before") or {}
    metrics_after = payload.get("metrics_after") or {}
    result = payload.get("result") or {}
    diagnoses_by_job = {
        d["job_id"]: d for d in (payload.get("diagnoses") or []) if d.get("job_id")
    }

    calendar = WorkingCalendar(date.today())
    completions_before = _job_completion_dates(tasks_before)
    jobs_after = {j["job_id"]: j for j in (result.get("jobs") or []) if j.get("job_id")}
    after_by_id = {t["activity_id"]: t for t in tasks_after if t.get("activity_id")}

    job_ids = list(payload.get("job_ids") or [])
    if not job_ids:
        job_ids = sorted(
            {
                *(t.get("job_id") for t in tasks_before if t.get("job_id")),
                *(t.get("job_id") for t in tasks_after if t.get("job_id")),
            }
        )

    penale_before = float(metrics_before.get("total_penalty") or 0)
    penale_after = float(
        metrics_after.get("total_penalty") or result.get("total_penalty") or 0
    )

    commesse: list[dict[str, Any]] = []
    for job_id in job_ids:
        deadline = _iso_day(deadlines.get(job_id))
        after_row = jobs_after.get(job_id) or {}
        commesse.append(
            {
                "job_id": job_id,
                "deadline": deadline,
                "attuale": _job_state_before(
                    completions_before.get(job_id), deadline, calendar
                ),
                "proposta": {
                    "fine_commessa": _iso_day(after_row.get("completion_date")),
                    "in_ritardo": int(after_row.get("delay_days") or 0) > 0,
                    "ritardo_giorni": after_row.get("delay_days", 0),
                    "penale_eur": round(float(after_row.get("penalty") or 0), 2),
                },
                "diagnosi": _diagnosis_entry(diagnoses_by_job.get(job_id)),
            }
        )

    all_changes = _collect_activity_changes(tasks_before, after_by_id, deadlines)
    omitted = 0
    attivita_modificate = all_changes
    if max_attivita_modificate is not None and max_attivita_modificate >= 0:
        omitted = max(0, len(all_changes) - max_attivita_modificate)
        attivita_modificate = all_changes[:max_attivita_modificate]

    n_activities = len(tasks_before)
    full_changed = len(all_changes)
    unchanged_count = n_activities - full_changed

    return {
        "meta": {
            "ok": True,
            "lingua": "it",
            "job_ids": job_ids,
            "legenda": {
                "attuale": "Pianificazione corrente nel database",
                "proposta": "Soluzione proposta dal solver di ottimizzazione",
                "penale_eur": "Penale stimata in euro per ritardo sulla deadline commessa",
                "risorse_joined": "Elenco risorse con separatore ; (convenzione applicazione)",
                "attivita_intoccabili": (
                    "Attività già iniziate o completate: escluse dal confronto "
                    "attuale/proposta ma considerate come vincoli fissi"
                ),
            },
        },
        "sintesi": {
            "penale_attuale_eur": round(penale_before, 2),
            "penale_proposta_eur": round(penale_after, 2),
            "delta_penale_eur": round(penale_after - penale_before, 2),
            "commesse_in_ritardo_attuale": metrics_before.get("num_late_jobs", 0),
            "commesse_in_ritardo_proposta": metrics_after.get(
                "num_late_jobs", result.get("num_late_jobs", 0)
            ),
            "attivita_in_ritardo_attuale": metrics_before.get("num_late_activities", 0),
            "attivita_in_ritardo_proposta": metrics_after.get("num_late_activities", 0),
            "attivita_totali": n_activities,
            "attivita_con_cambiamento": full_changed,
            "attivita_senza_cambiamento": unchanged_count,
        },
        "commesse": commesse,
        "riepilogo_per_commessa": [
            _job_change_summary(job_id, all_changes) for job_id in job_ids
        ],
        "attivita_modificate": attivita_modificate,
        "attivita_invariate": {
            "conteggio": unchanged_count,
            "nota": "Attività omesse dal dettaglio perché inizio, fine e risorse sono invariati.",
            **({"attivita_modificate_omesse": omitted} if omitted else {}),
        },
        "attivita_intoccabili": {
            "conteggio": len(fixed_activities),
            "nota": (
                "Attività già iniziate o completate: non compaiono nel confronto "
                "attuale/proposta; le risorse restano bloccate nell'intervallo effettivo."
            ),
            "attivita": fixed_activities,
        },
    }


def format_schedule_result_for_llm_json(
    job_ids: list[str],
    result: ScheduleResult,
    diagnoses: Iterable[JobDiagnosis],
    *,
    max_attivita_modificate: int | None = None,
    plant_colors: dict[str, str] | None = None,
    indent: int | None = 2,
) -> str:
    """Notebook shortcut: ``schedule`` output → the LLM diff as a JSON string."""
    payload = build_result_payload_from_schedule(
        job_ids, result, diagnoses, plant_colors=plant_colors
    )
    data = format_optimization_result_for_llm(
        payload, max_attivita_modificate=max_attivita_modificate
    )
    return json.dumps(data, ensure_ascii=False, indent=indent)


def generate_optimization_explanation(result_payload: dict[str, Any]) -> str:
    """Explain a ``result_payload`` in Italian through the explanation agent.

    The agent's system prompt carries the instructions; the user message is
    only the compact JSON diff.

    Raises:
        ValueError: When the payload is incomplete.
    """
    llm_data = format_optimization_result_for_llm(result_payload)
    meta = llm_data.get("meta", {})
    if not meta.get("ok"):
        raise ValueError(meta.get("errore", "Payload non valido per la spiegazione."))
    from src.optimization.explain_agent import ExplainResultsAgent

    return ExplainResultsAgent().run(json.dumps(llm_data, ensure_ascii=False, indent=2))
