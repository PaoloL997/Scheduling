"""Tests for output models and diagnostics (Phase 5)."""

from __future__ import annotations

from datetime import date

from src.optimization.cp_scheduler import solve
from src.optimization.data_model import ResourceType, SchedulingProblem
from src.optimization.diagnostics import diagnose
from src.optimization.output_model import build_schedule_result
from src.optimization.tests.builders import (
    make_activity,
    make_job,
    make_resource,
)

FAR_DUE = date(2030, 1, 1)


def _problem(jobs, resources, horizon_days=None):
    return SchedulingProblem(
        jobs=jobs,
        resources=resources,
        horizon_start=date(2026, 1, 1),
        horizon_days=horizon_days,
    )


def test_schedule_result_shape_and_dates():
    a1 = make_activity("a1", "j1", 1, duration_days=2)
    a2 = make_activity("a2", "j1", 2, duration_days=1)
    problem = _problem([make_job("j1", [a1, a2], due_date=FAR_DUE)], [])
    outcome = solve(problem)
    result = build_schedule_result(problem, outcome)

    assert {a.activity_id for a in result.activities} == {"a1", "a2"}
    assert len(result.jobs) == 1
    assert result.total_penalty == 0.0
    assert result.num_late_jobs == 0
    a1_sched = next(a for a in result.activities if a.activity_id == "a1")
    assert a1_sched.start_date <= a1_sched.end_date


def test_schedule_result_reports_penalty_and_late_jobs():
    a1 = make_activity("a1", "j1", 1, duration_days=1, earliest_start=date(2026, 1, 8))
    job = make_job("j1", [a1], due_date=date(2026, 1, 1), penalty_per_week=100)
    problem = _problem([job], [])
    outcome = solve(problem)
    result = build_schedule_result(problem, outcome)

    jr = result.jobs[0]
    assert jr.delay_days == 7
    assert jr.delay_weeks == 1
    assert jr.penalty == 100.0
    assert result.total_penalty == 100.0
    assert result.num_late_jobs == 1


def test_diagnosis_chain_too_long():
    a1 = make_activity("a1", "j1", 1, duration_days=20)
    job = make_job("j1", [a1], due_date=date(2026, 1, 2))
    problem = _problem([job], [])
    outcome = solve(problem)
    diag = {d.job_id: d for d in diagnose(problem, outcome)}["j1"]
    assert diag.late
    assert diag.primary_cause == "chain_too_long"


def test_diagnosis_earliest_start():
    a1 = make_activity("a1", "j1", 1, duration_days=1, earliest_start=date(2026, 2, 1))
    job = make_job("j1", [a1], due_date=date(2026, 1, 2))
    problem = _problem([job], [])
    outcome = solve(problem)
    diag = {d.job_id: d for d in diagnose(problem, outcome)}["j1"]
    assert diag.late
    assert diag.primary_cause == "earliest_start"


def test_diagnosis_resource_contention():
    # Two jobs share a single mandrino/tastatore; each alone fits before due.
    a1 = make_activity("a1", "j1", 1, duration_days=3, bore_min=10, bore_max=20)
    a2 = make_activity("a2", "j2", 1, duration_days=3, bore_min=10, bore_max=20)
    resources = [
        make_resource("m1", ResourceType.MANDRINO, bore=15),
        make_resource("t1", ResourceType.TASTATORE, bore=15),
    ]
    # Due date allows one chain (3 working days) but not two sequentially.
    due = date(2026, 1, 6)  # Tuesday: indexes 0..2 -> finishes 2026-01-06
    jobs = [
        make_job("j1", [a1], due_date=due),
        make_job("j2", [a2], due_date=due),
    ]
    problem = _problem(jobs, resources)
    outcome = solve(problem)
    diags = {d.job_id: d for d in diagnose(problem, outcome)}
    # Exactly one job is late, and its cause is resource contention.
    late = [d for d in diags.values() if d.late]
    assert len(late) == 1
    assert late[0].primary_cause == "resource_contention"


def test_diagnose_all_on_time():
    a1 = make_activity("a1", "j1", 1, duration_days=1)
    problem = _problem([make_job("j1", [a1], due_date=FAR_DUE)], [])
    outcome = solve(problem)
    diag = diagnose(problem, outcome)[0]
    assert not diag.late
    assert diag.primary_cause is None
