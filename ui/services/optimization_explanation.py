"""Format optimization results as a compact diff for LLM explanation."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date
from typing import Any

from src.optimization.calendar import WorkingCalendar
from ui.services.parsing import parse_iso_day
from src.optimization.diagnostics import JobDiagnosis
from src.optimization.output_model import ScheduleResult

_CAUSA_LABELS: dict[str, str] = {
    "resource_contention": "contesa_risorse",
    "earliest_start": "vincolo_inizio_minimo",
    "chain_too_long": "catena_troppo_lunga",
}

_DEFAULT_SETTINGS_MODULE = "config.settings"


def ensure_django(*, settings_module: str = _DEFAULT_SETTINGS_MODULE) -> None:
    """Initialize Django ORM (notebook/script helper).

    Call once before ``build_scheduling_problem`` or
    ``build_result_payload_from_schedule`` when running outside ``manage.py``.
    """
    import os

    import django
    from django.conf import settings

    if settings.configured:
        return
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)
    django.setup()


def _iso_day(value: Any) -> str | None:
    if not value:
        return None
    return str(value)[:10]


def _parse_day(value: Any) -> date | None:
    return parse_iso_day(value)


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


def _resources_joined(resources: list[str]) -> str | None:
    return ";".join(resources) if resources else None


def _day_delta(before: str | None, after: str | None) -> int | None:
    start = _parse_day(before)
    end = _parse_day(after)
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
        finish = _parse_day(task.get("finish"))
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
    completion = _parse_day(completion_date)
    due = _parse_day(deadline)
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
    task: dict[str, Any],
    deadlines: dict[str, str],
) -> dict[str, Any]:
    resources = _task_resources(task)
    job_id = task.get("job_id")
    finish = _iso_day(task.get("finish"))
    return {
        "inizio": _iso_day(task.get("start")),
        "fine": finish,
        "risorse": resources,
        "risorse_joined": _resources_joined(resources),
        "oltre_deadline": _is_late_finish(finish, job_id, deadlines),
    }


def _activity_delta(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    before_resources = before["risorse"]
    after_resources = after["risorse"]
    added = [r for r in after_resources if r not in before_resources]
    removed = [r for r in before_resources if r not in after_resources]
    return {
        "inizio_giorni": _day_delta(before["inizio"], after["inizio"]),
        "fine_giorni": _day_delta(before["fine"], after["fine"]),
        "risorse_cambiate": before_resources != after_resources,
        "risorse_aggiunte": added,
        "risorse_rimosse": removed,
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
        if not activity_id:
            continue
        after_task = after_by_id.get(activity_id)
        if after_task is None:
            continue

        before_snap = _activity_snapshot(before_task, deadlines)
        after_snap = _activity_snapshot(after_task, deadlines)
        if (
            before_snap["inizio"] == after_snap["inizio"]
            and before_snap["fine"] == after_snap["fine"]
            and before_snap["risorse"] == after_snap["risorse"]
        ):
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
    job_id: str,
    activity_changes: list[dict[str, Any]],
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


def _default_plant_colors() -> dict[str, str]:
    from ui.models import Stabilimento

    return {s.nome: s.colore for s in Stabilimento.objects.all()}


def _diagnoses_to_dicts(diagnoses: Iterable[JobDiagnosis]) -> list[dict[str, Any]]:
    return [
        {
            "job_id": d.job_id,
            "late": d.late,
            "delay_days": d.delay_days,
            "primary_cause": d.primary_cause,
            "detail": d.detail,
        }
        for d in diagnoses
    ]


def _schedule_result_to_dict(result: ScheduleResult) -> dict[str, Any]:
    return {
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
    }


def build_result_payload_from_schedule(
    job_ids: list[str],
    result: ScheduleResult,
    diagnoses: Iterable[JobDiagnosis],
    *,
    plant_colors: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a session-style ``result_payload`` from a solved ``ScheduleResult``.

    Use this after ``result, diagnoses = schedule(problem)`` before calling
    :func:`format_optimization_result_for_llm` or :func:`format_optimization_result_for_llm_json`.

    Args:
        job_ids: Commesse incluse nel problema (es. ``[j.job_id for j in problem.jobs]``).
        result: Output fattibile di :func:`src.optimization.solver.schedule`.
        diagnoses: Lista diagnosi restituita insieme al result.
        plant_colors: Mappa stabilimento → colore per i task Gantt; default da DB.

    Returns:
        Dict compatibile con :func:`format_optimization_result_for_llm`.
    """
    ensure_django()
    from ui.services.optimization_problem import (
        activities_for_job_ids,
        activities_to_gantt_tasks,
        deadlines_for_jobs,
        filter_plannable_gantt_tasks,
        fixed_activities_for_llm,
        is_attivita_fixed,
        schedule_metrics_from_tasks,
        schedule_result_to_gantt_tasks,
    )

    unique_job_ids = list(dict.fromkeys(job_ids))
    colors = plant_colors if plant_colors is not None else _default_plant_colors()
    attivita = activities_for_job_ids(unique_job_ids)
    attivita_by_id = {a.activity_id: a for a in attivita}
    if not attivita:
        raise ValueError(
            f"Nessuna attività nel database per job_ids={unique_job_ids!r}. "
            "Verifica commesse e che Django punti al database corretto."
        )

    tasks_before_all = activities_to_gantt_tasks(attivita, colors)
    tasks_before = filter_plannable_gantt_tasks(tasks_before_all, attivita)
    fixed_activities = fixed_activities_for_llm(attivita, colors)
    tasks_after_all = schedule_result_to_gantt_tasks(
        result,
        attivita_by_id,
        colors,
        attivita=attivita,
    )
    tasks_after = filter_plannable_gantt_tasks(tasks_after_all, attivita)
    fixed_tasks = activities_to_gantt_tasks(
        [a for a in attivita if is_attivita_fixed(a)],
        colors,
    )
    if not tasks_after and result.activities:
        from ui.services.optimization_problem import (
            match_attivita_for_solver_activity_id,
        )

        missing = [
            sched.activity_id
            for sched in result.activities
            if match_attivita_for_solver_activity_id(sched.activity_id, attivita)
            is None
        ]
        raise ValueError(
            "tasks_after vuoto: activity_id del solver non collegati ad attività nel DB "
            f"(es. {missing[:5]}). Passa job_ids come commesse del problem "
            "(es. [j.job_id for j in problem.jobs]) e assicurati che il problem "
            "sia stato costruito dallo stesso database usato da Django."
        )
    deadlines = deadlines_for_jobs(unique_job_ids)
    metrics_before = schedule_metrics_from_tasks(tasks_before_all, unique_job_ids)
    metrics_after = schedule_metrics_from_tasks(
        tasks_after + fixed_tasks,
        unique_job_ids,
    )

    return {
        "tasks_before": tasks_before,
        "tasks_after": tasks_after,
        "fixed_activities": fixed_activities,
        "deadlines": deadlines,
        "job_ids": unique_job_ids,
        "metrics_before": metrics_before,
        "metrics_after": {
            "total_penalty": result.total_penalty,
            "num_late_jobs": result.num_late_jobs,
            "num_late_activities": metrics_after["num_late_activities"],
            "n_activities": metrics_after["n_activities"],
        },
        "result": _schedule_result_to_dict(result),
        "diagnoses": _diagnoses_to_dicts(diagnoses),
    }


def _payload_input_hint(value: Any) -> str:
    if isinstance(value, ScheduleResult):
        return (
            "Hai passato ScheduleResult. Usa: "
            "format_schedule_result_for_llm_json(job_ids, result, diagnoses)"
        )
    if not isinstance(value, dict):
        return f"Tipo ricevuto: {type(value).__name__}. Atteso result_payload (dict)."

    keys = sorted(value.keys())
    if "result_payload" in keys:
        return "Hai passato l'intero outcome: usa outcome['result_payload']."
    if "activities" in keys and "jobs" in keys and "tasks_before" not in keys:
        return (
            "Sembra ScheduleResult.model_dump(). Usa "
            "format_schedule_result_for_llm_json(job_ids, result, diagnoses)."
        )
    if "total_penalty" in keys and "jobs" in keys and "tasks_before" not in keys:
        return (
            "Hai passato solo payload['result']. Usa "
            "format_schedule_result_for_llm_json(job_ids, result, diagnoses)."
        )
    return (
        f"Chiavi ricevute: {keys}. Servono tasks_before e tasks_after "
        "(build_result_payload_from_schedule o format_schedule_result_for_llm_json)."
    )


def resolve_llm_payload(
    data: dict[str, Any] | ScheduleResult,
    *,
    job_ids: list[str] | None = None,
    diagnoses: Iterable[JobDiagnosis] | None = None,
) -> dict[str, Any]:
    """Normalize solver output / session data into a ``result_payload`` dict."""
    if isinstance(data, ScheduleResult):
        if job_ids is None:
            raise TypeError(
                "Passato ScheduleResult: fornisci job_ids= e diagnoses=, oppure usa "
                "format_schedule_result_for_llm_json(job_ids, result, diagnoses)."
            )
        return build_result_payload_from_schedule(
            job_ids,
            data,
            diagnoses or [],
        )

    payload = data
    if isinstance(payload, dict) and "result_payload" in payload:
        payload = payload["result_payload"]

    if not isinstance(payload, dict):
        raise TypeError(_payload_input_hint(data))

    if payload.get("tasks_before") and payload.get("tasks_after"):
        return payload

    raise TypeError(_payload_input_hint(payload))


def format_optimization_result_for_llm(
    payload: dict[str, Any],
    *,
    max_attivita_modificate: int | None = None,
) -> dict[str, Any]:
    """Build a compact before/after diff from an optimization ``result_payload``.

    The returned dict is meant to be passed to an LLM (as JSON) for natural-language
    explanation of what changed between the current plan and the solver proposal.

    Args:
        payload: Session ``result_payload`` with ``tasks_before``, ``tasks_after``,
            ``deadlines``, ``metrics_before``, ``metrics_after``, ``result``,
            ``diagnoses``, and ``job_ids``.
        max_attivita_modificate: If set, keep only the activities with the largest
            absolute change in ``fine`` (ties broken by ``inizio``).

    Returns:
        Structured summary suitable for LLM input. On infeasible or incomplete
        payloads, ``meta.ok`` is False and ``meta.errore`` explains why.
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
                "suggerimento": _payload_input_hint(payload),
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
                    completions_before.get(job_id),
                    deadline,
                    calendar,
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


def format_optimization_result_for_llm_json(
    payload: dict[str, Any] | ScheduleResult,
    *,
    job_ids: list[str] | None = None,
    diagnoses: Iterable[JobDiagnosis] | None = None,
    max_attivita_modificate: int | None = None,
    indent: int | None = 2,
) -> str:
    """Return :func:`format_optimization_result_for_llm` output as a JSON string.

    Accepts a full ``result_payload``, an optimization ``outcome`` dict
    (with ``result_payload``), or a ``ScheduleResult`` if ``job_ids`` and
    ``diagnoses`` are also provided.
    """
    resolved = resolve_llm_payload(
        payload,
        job_ids=job_ids,
        diagnoses=diagnoses,
    )
    data = format_optimization_result_for_llm(
        resolved,
        max_attivita_modificate=max_attivita_modificate,
    )
    return json.dumps(data, ensure_ascii=False, indent=indent)


def format_schedule_result_for_llm(
    job_ids: list[str],
    result: ScheduleResult,
    diagnoses: Iterable[JobDiagnosis],
    *,
    max_attivita_modificate: int | None = None,
    plant_colors: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Shortcut: ``schedule`` output → LLM-ready dict."""
    payload = build_result_payload_from_schedule(
        job_ids,
        result,
        diagnoses,
        plant_colors=plant_colors,
    )
    return format_optimization_result_for_llm(
        payload,
        max_attivita_modificate=max_attivita_modificate,
    )


def format_schedule_result_for_llm_json(
    job_ids: list[str],
    result: ScheduleResult,
    diagnoses: Iterable[JobDiagnosis],
    *,
    max_attivita_modificate: int | None = None,
    plant_colors: dict[str, str] | None = None,
    indent: int | None = 2,
) -> str:
    """Shortcut: ``schedule`` output → LLM-ready JSON string."""
    data = format_schedule_result_for_llm(
        job_ids,
        result,
        diagnoses,
        max_attivita_modificate=max_attivita_modificate,
        plant_colors=plant_colors,
    )
    return json.dumps(data, ensure_ascii=False, indent=indent)


def generate_optimization_explanation(result_payload: dict[str, Any]) -> str:
    """Generate a textual explanation of optimization results via ExplainResultsAgent.

    The agent's system prompt already contains instructions; this function passes
    only the compact LLM JSON as the user message.
    """
    llm_data = format_optimization_result_for_llm(result_payload)
    if not llm_data.get("meta", {}).get("ok"):
        raise ValueError(
            llm_data.get("meta", {}).get(
                "errore", "Payload non valido per la spiegazione."
            )
        )
    llm_json = format_optimization_result_for_llm_json(result_payload)
    from src.optimization.explain_agent import ExplainResultsAgent

    return ExplainResultsAgent().run(llm_json)
