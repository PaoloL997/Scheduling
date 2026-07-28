"""Views for the optimization scheduling wizard."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.timezone import localdate
from django.views.decorators.http import require_http_methods

from src.optimization.feasibility import check_feasibility
from src.optimization.solver import schedule

from ui.models import Stabilimento
from ui.services.runtime_config import load_runtime_config
from ui.services.optimization_problem import (
    OptimizationProblemError,
    activities_for_job_ids,
    activities_to_gantt_tasks,
    build_scheduling_problem,
    deadlines_for_jobs,
    filter_job_ids_with_activities,
    filter_plannable_gantt_tasks,
    fixed_activities_for_llm,
    hydrate_gantt_tasks_resources,
    is_attivita_fixed,
    schedule_metrics_from_tasks,
    schedule_result_to_gantt_tasks,
)
from ui.services.optimization_apply import (
    OptimizationApplyError,
    apply_optimization_result,
)
from ui.services.optimization_explanation import generate_optimization_explanation
from ui.services.resource_conflicts import (
    ResourceConflictError,
    format_conflict_message,
)
from ui.views import _fetch_grouped, _has_plannable_activities

logger = logging.getLogger(__name__)

SESSION_DRAFT_KEY = "ottimizzazione_draft"
SESSION_RESULT_KEY = "ottimizzazione_result"
SESSION_EXPLANATION_KEY = "ottimizzazione_explanation"


def _plant_colors() -> dict[str, str]:
    return {s.nome: s.colore for s in Stabilimento.objects.all()}


def _parse_request_body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return {}


def _parse_date_field(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_draft(data: dict) -> tuple[list[str], dict[str, date]]:
    job_ids = [str(j).strip() for j in data.get("job_ids") or [] if str(j).strip()]
    overrides: dict[str, date] = {}
    for aid, raw in (data.get("earliest_start_overrides") or {}).items():
        parsed = _parse_date_field(raw)
        if parsed is not None:
            overrides[str(aid).strip()] = parsed
    return job_ids, overrides


def _parse_optimization_payload(
    data: dict,
) -> tuple[list[str], dict[str, date], date | None]:
    job_ids, overrides = _parse_draft(data)
    horizon_start = _parse_date_field(data.get("horizon_start"))
    return job_ids, overrides, horizon_start


def _store_draft(request: HttpRequest, draft: dict) -> None:
    request.session[SESSION_DRAFT_KEY] = draft
    request.session.modified = True


def _get_draft(request: HttpRequest) -> dict | None:
    draft = request.session.get(SESSION_DRAFT_KEY)
    if not draft or not draft.get("job_ids"):
        return None
    return draft


def _store_optimization_result(
    request: HttpRequest,
    payload: dict,
    *,
    explanation: str | None = None,
) -> None:
    request.session[SESSION_RESULT_KEY] = payload
    if explanation is not None:
        request.session[SESSION_EXPLANATION_KEY] = explanation
    else:
        request.session.pop(SESSION_EXPLANATION_KEY, None)
    request.session.modified = True


def _fallback_explanation(reason: str) -> str:
    """Build a minimal markdown note when the LLM explanation is unavailable."""
    return (
        "## Risultato ottimizzazione\n\n"
        "La proposta di pianificazione è disponibile qui sotto.\n\n"
        "### Spiegazione automatica non disponibile\n\n"
        f"{reason}\n"
    )


def _complete_optimization_session(
    request: HttpRequest,
    result_payload: dict,
) -> str | None:
    """Store optimization result and explanation.

    The optimization result is always stored. If the LLM explanation fails, a
    fallback note is saved so the results page remains usable.

    Returns:
        Optional warning message (``None`` when the LLM explanation succeeded).
    """
    try:
        explanation = generate_optimization_explanation(result_payload)
        warning = None
    except (RuntimeError, ValueError) as exc:
        logger.warning("Spiegazione ottimizzazione non disponibile: %s", exc)
        explanation = _fallback_explanation(str(exc))
        warning = str(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore imprevisto durante la generazione della spiegazione")
        explanation = _fallback_explanation(
            "Errore durante la generazione della spiegazione."
        )
        warning = "Errore durante la generazione della spiegazione."
    _store_optimization_result(request, result_payload, explanation=explanation)
    return warning


def _clear_wizard_session(request: HttpRequest) -> None:
    request.session.pop(SESSION_DRAFT_KEY, None)
    request.session.pop(SESSION_RESULT_KEY, None)
    request.session.pop(SESSION_EXPLANATION_KEY, None)
    request.session.modified = True


def _optimization_groups() -> list[dict]:
    """Return grouped commesse that have at least one plannable activity."""
    return [g for g in _fetch_grouped() if _has_plannable_activities(g["activities"])]


def _sanitize_draft(draft: dict) -> dict:
    """Drop job ids that no longer resolve to activities in the database."""
    job_ids = [str(j).strip() for j in draft.get("job_ids") or [] if str(j).strip()]
    if not job_ids:
        return {}
    resolved = filter_job_ids_with_activities(job_ids)
    unplannable_ids = {
        g["job"]["job_id"]
        for g in _fetch_grouped()
        if g["job"].get("job_id") and not _has_plannable_activities(g["activities"])
    }
    resolved = [jid for jid in resolved if jid not in unplannable_ids]
    if not resolved:
        return {}
    overrides = draft.get("earliest_start_overrides") or {}
    return {
        "job_ids": resolved,
        "earliest_start_overrides": overrides,
        "horizon_start": draft.get("horizon_start"),
    }


def _draft_summary(draft: dict) -> dict[str, Any]:
    job_ids = draft.get("job_ids") or []
    overrides = draft.get("earliest_start_overrides") or {}
    attivita = activities_for_job_ids(job_ids)
    return {
        "n_jobs": len(job_ids),
        "n_activities": len(attivita),
        "n_constraints": len(overrides),
    }


def _run_optimization(
    job_ids: list[str],
    earliest_start_overrides: dict[str, date],
    horizon_start: date | None,
) -> dict[str, Any]:
    """Run feasibility check and solver; return a result dict."""
    try:
        problem = build_scheduling_problem(
            job_ids,
            earliest_start_overrides=earliest_start_overrides,
            horizon_start=horizon_start,
        )
    except OptimizationProblemError as exc:
        return {"ok": False, "feasible": False, "error": str(exc)}

    time_limit = load_runtime_config().solver_time_limit
    feasibility = check_feasibility(problem)
    colors = _plant_colors()
    attivita = activities_for_job_ids(job_ids)
    attivita_by_id = {a.activity_id: a for a in attivita}
    tasks_before_all = activities_to_gantt_tasks(attivita, colors)
    tasks_before = filter_plannable_gantt_tasks(tasks_before_all, attivita)
    fixed_activities = fixed_activities_for_llm(attivita, colors)
    deadlines = deadlines_for_jobs(job_ids)

    if not feasibility.feasible:
        return {
            "ok": True,
            "feasible": False,
            "error": "; ".join(i.detail for i in feasibility.issues),
            "tasks_before": tasks_before,
            "fixed_activities": fixed_activities,
            "deadlines": deadlines,
        }

    result, diagnoses = schedule(problem, time_limit=time_limit)
    if result is None:
        return {
            "ok": True,
            "feasible": False,
            "error": "Il solver non ha trovato una soluzione fattibile.",
            "tasks_before": tasks_before,
            "fixed_activities": fixed_activities,
            "deadlines": deadlines,
        }

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
    metrics_before = schedule_metrics_from_tasks(tasks_before_all, job_ids)
    metrics_after_from_tasks = schedule_metrics_from_tasks(
        tasks_after + fixed_tasks,
        job_ids,
    )
    return {
        "ok": True,
        "feasible": True,
        "result_payload": {
            "tasks_before": tasks_before,
            "tasks_after": tasks_after,
            "fixed_activities": fixed_activities,
            "deadlines": deadlines,
            "job_ids": job_ids,
            "metrics_before": metrics_before,
            "metrics_after": {
                "total_penalty": result.total_penalty,
                "num_late_jobs": result.num_late_jobs,
                "num_late_activities": metrics_after_from_tasks["num_late_activities"],
                "n_activities": metrics_after_from_tasks["n_activities"],
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
        },
    }


@login_required
def ottimizzazione(request: HttpRequest) -> HttpResponse:
    """Commessa selection, constraints, and horizon — single wizard step."""
    today = localdate()
    horizon_default = (today + timedelta(days=1)).isoformat()
    raw_draft = request.session.get(SESSION_DRAFT_KEY) or {}
    draft = _sanitize_draft(raw_draft)
    if draft != raw_draft:
        if draft.get("job_ids"):
            _store_draft(request, draft)
        else:
            request.session.pop(SESSION_DRAFT_KEY, None)
            request.session.modified = True
    horizon = draft.get("horizon_start") or horizon_default
    return render(
        request,
        "ui/ottimizzazione_commesse.html",
        {
            "groups_json": json.dumps(_optimization_groups()),
            "draft_json": json.dumps(draft),
            "horizon_default": horizon,
            "solver_time_limit": load_runtime_config().solver_time_limit,
        },
    )


@login_required
def ottimizzazione_parametri(request: HttpRequest) -> HttpResponse:
    """Deprecated step 2 — redirect to commesse selection."""
    return redirect("ottimizzazione")


@login_required
@require_http_methods(["POST"])
def ottimizzazione_run(request: HttpRequest) -> HttpResponse:
    """Run optimization from commesse step and redirect to results."""
    raw = request.POST.get("draft_json", "")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        messages.error(request, "Dati non validi.")
        return redirect("ottimizzazione")

    job_ids, overrides, horizon_start = _parse_optimization_payload(data)
    resolved_job_ids = filter_job_ids_with_activities(job_ids)
    dropped_job_ids = [jid for jid in job_ids if jid not in set(resolved_job_ids)]
    if dropped_job_ids:
        preview = ", ".join(dropped_job_ids[:5])
        if len(dropped_job_ids) > 5:
            preview += "…"
        messages.warning(
            request,
            f"Commesse senza attività ignorate: {preview}",
        )
    job_ids = resolved_job_ids
    if not job_ids:
        messages.error(request, "Seleziona almeno una commessa.")
        return redirect("ottimizzazione")

    draft = {
        "job_ids": job_ids,
        "earliest_start_overrides": {k: v.isoformat() for k, v in overrides.items()},
        "horizon_start": horizon_start.isoformat() if horizon_start else None,
    }
    _store_draft(request, draft)

    outcome = _run_optimization(job_ids, overrides, horizon_start)
    if not outcome.get("ok"):
        messages.error(request, outcome.get("error", "Errore ottimizzazione."))
        return redirect("ottimizzazione")

    if not outcome.get("feasible"):
        messages.error(
            request,
            outcome.get("error", "Nessuna soluzione fattibile trovata."),
        )
        return redirect("ottimizzazione")

    explain_warning = _complete_optimization_session(request, outcome["result_payload"])
    if explain_warning:
        messages.warning(request, explain_warning)
    return redirect("ottimizzazione_risultati")


@login_required
def ottimizzazione_risultati(request: HttpRequest) -> HttpResponse:
    """Step 3: full-screen before/after comparison."""
    payload = request.session.get(SESSION_RESULT_KEY)
    explanation = request.session.get(SESSION_EXPLANATION_KEY)
    if not payload or not explanation:
        return redirect("ottimizzazione")
    tasks_before = payload.get("tasks_before")
    if tasks_before:
        hydrate_gantt_tasks_resources(tasks_before)
        payload["tasks_before"] = tasks_before
        request.session[SESSION_RESULT_KEY] = payload
        request.session.modified = True
    return render(
        request,
        "ui/ottimizzazione_risultati.html",
        {
            "result_json": json.dumps(payload),
            "explanation_json": json.dumps(explanation),
        },
    )


@login_required
def ottimizzazione_nuova(request: HttpRequest) -> HttpResponse:
    """Clear wizard state and start a new optimization run."""
    _clear_wizard_session(request)
    return redirect("ottimizzazione")


@login_required
@require_http_methods(["POST"])
def api_ottimizzazione_solve(request: HttpRequest) -> JsonResponse:
    """JSON API for optimization (used by tests and programmatic clients)."""
    data = _parse_request_body(request)
    job_ids, overrides, horizon_start = _parse_optimization_payload(data)
    job_ids = filter_job_ids_with_activities(job_ids)
    if not job_ids:
        return JsonResponse(
            {"ok": False, "error": "Seleziona almeno una commessa."},
            status=400,
        )

    outcome = _run_optimization(job_ids, overrides, horizon_start)
    if not outcome.get("ok"):
        return JsonResponse(
            {"ok": False, "error": outcome.get("error", "Errore.")},
            status=400,
        )

    if not outcome.get("feasible"):
        return JsonResponse(
            {
                "ok": True,
                "feasible": False,
                "tasks_before": outcome.get("tasks_before", []),
                "tasks_after": [],
                "deadlines": outcome.get("deadlines", {}),
                "error": outcome.get("error"),
            }
        )

    explain_warning = _complete_optimization_session(request, outcome["result_payload"])

    payload = {
        "ok": True,
        "feasible": True,
        "redirect_url": reverse("ottimizzazione_risultati"),
    }
    if explain_warning:
        payload["warning"] = explain_warning
    return JsonResponse(payload)


@login_required
@require_http_methods(["POST"])
def api_ottimizzazione_spiegazione(request: HttpRequest) -> JsonResponse:
    """Generate (or return cached) textual explanation of optimization results."""
    payload = request.session.get(SESSION_RESULT_KEY)
    if not payload:
        return JsonResponse(
            {"ok": False, "error": "Nessun risultato di ottimizzazione in sessione."},
            status=400,
        )

    cached = request.session.get(SESSION_EXPLANATION_KEY)
    if cached:
        return JsonResponse({"ok": True, "explanation": cached, "cached": True})

    try:
        explanation = generate_optimization_explanation(payload)
    except RuntimeError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=503)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception:
        return JsonResponse(
            {
                "ok": False,
                "error": "Errore durante la generazione della spiegazione.",
            },
            status=500,
        )

    request.session[SESSION_EXPLANATION_KEY] = explanation
    request.session.modified = True
    return JsonResponse({"ok": True, "explanation": explanation, "cached": False})


@login_required
@require_http_methods(["POST"])
def api_ottimizzazione_accetta(request: HttpRequest) -> JsonResponse:
    """Apply the proposed optimization schedule to planning records."""
    payload = request.session.get(SESSION_RESULT_KEY)
    if not payload:
        return JsonResponse(
            {"ok": False, "error": "Nessun risultato di ottimizzazione in sessione."},
            status=400,
        )

    try:
        updated = apply_optimization_result(payload)
    except ResourceConflictError as exc:
        return JsonResponse(
            {
                "ok": False,
                "error": format_conflict_message(
                    exc.conflicts,
                    action_hint=(
                        "Rivedi le date o le risorse delle attività coinvolte "
                        "prima di applicare la proposta."
                    ),
                ),
                "conflicts": exc.conflicts,
            },
            status=409,
        )
    except OptimizationApplyError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception:
        return JsonResponse(
            {"ok": False, "error": "Errore durante l'applicazione della proposta."},
            status=500,
        )

    _clear_wizard_session(request)
    return JsonResponse(
        {
            "ok": True,
            "updated": updated,
            "redirect_url": reverse("pianificazione"),
        }
    )
