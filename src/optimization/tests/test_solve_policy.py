"""Solver policy: no job is ignored, lateness is minimised in days too, and
the time limit is a budget for the whole run."""

from __future__ import annotations

import time
from datetime import date

from src.optimization.cp_scheduler import solve
from src.optimization.data_model import ResourceType, SchedulingProblem
from src.optimization.output_model import build_schedule_result
from src.optimization.tests.builders import make_activity, make_job, make_resource


def _problem(jobs, resources=None, horizon_start=date(2026, 1, 5), **kwargs):
    return SchedulingProblem(
        jobs=jobs,
        resources=resources or [],
        horizon_start=horizon_start,
        **kwargs,
    )


def test_job_without_penalty_is_still_scheduled_as_early_as_possible():
    """Two jobs, one shared mandrino, neither carries a penalty."""
    start = date(2026, 1, 5)  # Monday
    a1 = make_activity("a1", "j1", 1, duration_days=2, bore_min=10, bore_max=20)
    a2 = make_activity("a2", "j2", 1, duration_days=2, bore_min=10, bore_max=20)
    problem = _problem(
        [
            make_job("j1", [a1], due_date=start, penalty_per_week=0),
            make_job("j2", [a2], due_date=start, penalty_per_week=0),
        ],
        [
            make_resource("m1", ResourceType.MANDRINO, bore=15),
            make_resource("t1", ResourceType.TASTATORE, bore=15),
            make_resource("t2", ResourceType.TASTATORE, bore=15),
        ],
        horizon_start=start,
        horizon_days=40,
    )
    outcome = solve(problem)
    assert outcome.feasible
    # The shared mandrino forces a sequence; the free horizon would allow
    # both to drift to the end, but late days are minimised: 0 and 2.
    assert sorted(outcome.activity_starts.values()) == [0, 2]
    assert outcome.total_penalty_cents == 0
    result = build_schedule_result(problem, outcome)
    assert result.total_penalty == 0.0


def test_real_penalty_outranks_a_job_without_one():
    start = date(2026, 1, 5)  # Monday
    paid = make_activity("p", "paid", 1, duration_days=3, bore_min=10, bore_max=20)
    free = make_activity("f", "free", 1, duration_days=3, bore_min=10, bore_max=20)
    # Paid first: ends Wednesday, on time. Free first: the paid job slips to
    # the next week and pays; the free job has no amount to weigh.
    problem = _problem(
        [
            make_job("free", [free], due_date=start, penalty_per_week=0),
            make_job("paid", [paid], due_date=date(2026, 1, 7), penalty_per_week=500),
        ],
        [
            make_resource("m1", ResourceType.MANDRINO, bore=15),
            make_resource("t1", ResourceType.TASTATORE, bore=15),
            make_resource("t2", ResourceType.TASTATORE, bore=15),
        ],
        horizon_start=start,
        horizon_days=40,
    )
    outcome = solve(problem)
    assert outcome.activity_starts["p"] == 0
    assert outcome.activity_starts["f"] == 3
    assert outcome.total_penalty_cents == 0


def test_late_days_are_minimised_within_the_same_week():
    """A job late by one or three days pays one week either way: pick one."""
    start = date(2026, 1, 5)  # Monday
    # Due Wednesday 7 Jan; the activity needs 3 working days, so it ends
    # Wednesday at the earliest: finishing later costs the same single week.
    a = make_activity("a", "j", 1, duration_days=3)
    problem = _problem(
        [make_job("j", [a], due_date=date(2026, 1, 6), penalty_per_week=100)],
        horizon_start=start,
        horizon_days=30,
    )
    outcome = solve(problem)
    assert outcome.activity_starts["a"] == 0
    result = build_schedule_result(problem, outcome)
    assert result.jobs[0].delay_days == 1
    assert result.jobs[0].delay_weeks == 1


def test_time_limit_bounds_the_whole_run():
    start = date(2026, 1, 5)
    jobs = []
    resources = [
        make_resource(f"m{i}", ResourceType.MANDRINO, bore=15) for i in range(4)
    ]
    resources += [
        make_resource(f"t{i}", ResourceType.TASTATORE, bore=15) for i in range(4)
    ]
    for j in range(8):
        acts = [
            make_activity(
                f"a{j}_{k}",
                f"j{j}",
                k + 1,
                duration_days=2,
                bore_min=10,
                bore_max=20,
                bore_mid=15 if k % 2 else 16,
            )
            for k in range(3)
        ]
        jobs.append(make_job(f"j{j}", acts, due_date=start, penalty_per_week=10 + j))
    problem = _problem(jobs, resources, horizon_start=start)
    t0 = time.monotonic()
    outcome = solve(problem, time_limit=2.0)
    elapsed = time.monotonic() - t0
    assert outcome.feasible
    # Five phases used to mean five times the limit; now the run as a whole
    # stays close to it (generous slack for process start-up on slow boxes).
    assert elapsed < 6.0


def test_generator_compatibility_travels_with_the_problem():
    start = date(2026, 1, 5)
    a = make_activity("a", "j", 1, duration_days=1, head_model="TP60")
    resources = [
        make_resource("h1", ResourceType.TESTA, model="TP60"),
        make_resource("g_ok", ResourceType.GENERATORE, model="P6"),
        make_resource("g_no", ResourceType.GENERATORE, model="SPX"),
    ]
    constrained = _problem(
        [make_job("j", [a], due_date=start)],
        resources,
        horizon_start=start,
        generator_compatibility={"TP60": ["P6"]},
    )
    outcome = solve(constrained)
    assert outcome.activity_resources["a"] == ["g_ok", "h1"] or sorted(
        outcome.activity_resources["a"]
    ) == ["g_ok", "h1"]

    unconstrained = _problem(
        [make_job("j", [a], due_date=start)], resources, horizon_start=start
    )
    outcome = solve(unconstrained)
    assert outcome.feasible
    assert len(outcome.activity_resources["a"]) == 2
