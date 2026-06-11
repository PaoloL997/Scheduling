"""Robustness / edge-case tests (Phase 6).

Covers: already-late job, no resource with bore_mid, a scarce resource shared by
many jobs, a binding earliest_start, and a horizon that is too short.
"""

from __future__ import annotations

from datetime import date

from src.optimization.cp_scheduler import solve
from src.optimization.data_model import ResourceType, SchedulingProblem
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


def test_job_already_late_returns_valid_schedule():
    a1 = make_activity("a1", "j1", 1, duration_days=2)
    job = make_job("j1", [a1], due_date=date(2019, 1, 1), penalty_per_week=500)
    problem = _problem([job], [], horizon_days=20)
    outcome = solve(problem)
    assert outcome.feasible
    result = build_schedule_result(problem, outcome)
    assert result.num_late_jobs == 1
    assert result.total_penalty > 0


def test_no_bore_mid_resource_uses_admissible_one():
    a1 = make_activity("a1", "j1", 1, bore_min=10, bore_mid=15, bore_max=20)
    resources = [
        make_resource("m1", ResourceType.MANDRINO, bore=11),  # no mid available
        make_resource("t1", ResourceType.TASTATORE, bore=11),
    ]
    outcome = solve(_problem([make_job("j1", [a1], due_date=FAR_DUE)], resources))
    assert outcome.feasible
    assert set(outcome.activity_resources["a1"]) == {"m1", "t1"}


def test_scarce_resource_shared_by_many_jobs_is_sequenced():
    jobs = []
    for i in range(4):
        act = make_activity(
            f"a{i}", f"j{i}", 1, duration_days=2, bore_min=10, bore_max=20
        )
        jobs.append(make_job(f"j{i}", [act], due_date=FAR_DUE))
    resources = [
        make_resource("m1", ResourceType.MANDRINO, bore=15),
        make_resource("t1", ResourceType.TASTATORE, bore=15),
    ]
    problem = _problem(jobs, resources)
    outcome = solve(problem)
    assert outcome.feasible
    # All four activities use the single shared pair -> intervals must not
    # overlap, so their [start, start+2) windows are pairwise disjoint.
    starts = sorted(outcome.activity_starts.values())
    for earlier, later in zip(starts, starts[1:]):
        assert later - earlier >= 2


def test_binding_earliest_start_causes_late_delivery():
    a1 = make_activity("a1", "j1", 1, duration_days=1, earliest_start=date(2026, 3, 2))
    job = make_job("j1", [a1], due_date=date(2026, 1, 5), penalty_per_week=100)
    problem = _problem([job], [])
    outcome = solve(problem)
    assert outcome.feasible
    result = build_schedule_result(problem, outcome)
    assert result.jobs[0].delay_days > 0


def test_horizon_too_short_is_reported_infeasible():
    a1 = make_activity("a1", "j1", 1, duration_days=10)
    outcome = solve(
        _problem([make_job("j1", [a1], due_date=FAR_DUE)], [], horizon_days=3)
    )
    assert not outcome.feasible
    assert outcome.status == "INFEASIBLE_PRECHECK"
    assert any(
        issue.kind == "horizon_too_short" for issue in outcome.feasibility_report.issues
    )
