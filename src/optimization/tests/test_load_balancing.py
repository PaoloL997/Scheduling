"""Tests for phase D: workload levelling (minimum peak of concurrent work).

Phase D minimizes the maximum number of activities (plus reservations)
running at the same time, without worsening penalty, bore-mid or
continuity results from the earlier phases.
"""

from __future__ import annotations

from datetime import date

from src.optimization.cp_scheduler import solve
from src.optimization.data_model import (
    ResourceReservation,
    ResourceType,
    SchedulingProblem,
)
from src.optimization.tests.builders import (
    make_activity,
    make_job,
    make_resource,
)

FAR_DUE = date(2030, 1, 1)


def _problem(jobs, resources, *, reservations=None, horizon_days=None):
    return SchedulingProblem(
        jobs=jobs,
        resources=resources,
        horizon_start=date(2026, 1, 1),
        horizon_days=horizon_days,
        resource_reservations=reservations or [],
    )


def test_independent_jobs_are_spread_when_slack_allows():
    # Two independent activities with no shared resources and far due dates:
    # phases A-C leave the starts free, so phase D must avoid the overlap.
    a1 = make_activity("a1", "j1", 1, duration_days=3)
    a2 = make_activity("a2", "j2", 1, duration_days=3)
    jobs = [
        make_job("j1", [a1], due_date=FAR_DUE),
        make_job("j2", [a2], due_date=FAR_DUE),
    ]
    outcome = solve(_problem(jobs, [], horizon_days=30))
    assert outcome.feasible
    s1 = outcome.activity_starts["a1"]
    s2 = outcome.activity_starts["a2"]
    # Peak 1 means the two intervals cannot overlap.
    assert s1 + 3 <= s2 or s2 + 3 <= s1


def test_tight_due_dates_keep_parallelism_and_zero_penalty():
    # Both jobs must run in the first week to avoid penalties: levelling is
    # subordinate to phase A, so the overlap (peak 2) is accepted.
    a1 = make_activity("a1", "j1", 1, duration_days=5)
    a2 = make_activity("a2", "j2", 1, duration_days=5)
    due = date(2026, 1, 8)  # only an immediate start meets this deadline
    jobs = [
        make_job("j1", [a1], due_date=due, penalty_per_week=100),
        make_job("j2", [a2], due_date=due, penalty_per_week=100),
    ]
    outcome = solve(_problem(jobs, [], horizon_days=30))
    assert outcome.feasible
    assert outcome.total_penalty_cents == 0
    assert outcome.activity_starts["a1"] == 0
    assert outcome.activity_starts["a2"] == 0


def test_reservation_counts_toward_peak():
    # The reservation occupies working indices [0, 4) on a resource the new
    # activity does not need: only the load peak links them, so phase D must
    # move the activity outside the busy window.
    a1 = make_activity("a1", "j1", 1, duration_days=2)
    resources = [make_resource("m1", ResourceType.MANDRINO, bore=15)]
    reservations = [
        ResourceReservation(
            resource_id="m1",
            start_index=0,
            duration_days=4,
            activity_id="in-progress-activity",
        )
    ]
    outcome = solve(
        _problem(
            [make_job("j1", [a1], due_date=FAR_DUE)],
            resources,
            reservations=reservations,
            horizon_days=30,
        )
    )
    assert outcome.feasible
    # Peak 1 requires the activity to start after the reservation ends.
    assert outcome.activity_starts["a1"] >= 4
