"""Tests for resource reservations from started/completed activities.

Started or completed activities are never part of the optimization model:
they only appear as ``ResourceReservation`` entries that block their
resources for the locked time window.
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
HORIZON = date(2026, 1, 1)


def _problem(jobs, resources, *, reservations=None, horizon_days=None):
    return SchedulingProblem(
        jobs=jobs,
        resources=resources,
        horizon_start=HORIZON,
        horizon_days=horizon_days,
        resource_reservations=reservations or [],
    )


def test_reservation_blocks_resource_window():
    a_free = make_activity("a1", "j1", 1, duration_days=2, bore_min=10, bore_max=20)
    resources = [
        make_resource("m1", ResourceType.MANDRINO, bore=15),
        make_resource("t1", ResourceType.TASTATORE, bore=15),
    ]
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
            [make_job("j1", [a_free], due_date=FAR_DUE)],
            resources,
            reservations=reservations,
        )
    )
    assert outcome.feasible
    # m1 is busy for working indices [0, 4): a1 must start at or after 4.
    assert outcome.activity_starts["a1"] >= 4


def test_reservation_on_unused_resource_is_harmless():
    a_free = make_activity("a1", "j1", 1, duration_days=2, bore_min=10, bore_max=20)
    resources = [
        make_resource("m1", ResourceType.MANDRINO, bore=15),
        make_resource("t1", ResourceType.TASTATORE, bore=15),
    ]
    reservations = [
        ResourceReservation(resource_id="other", start_index=0, duration_days=30)
    ]
    outcome = solve(
        _problem(
            [make_job("j1", [a_free], due_date=FAR_DUE)],
            resources,
            reservations=reservations,
        )
    )
    assert outcome.feasible
    assert outcome.activity_starts["a1"] == 0


def test_multiple_reservations_force_late_start():
    a_free = make_activity("a1", "j1", 1, duration_days=1, bore_min=10, bore_max=20)
    resources = [
        make_resource("m1", ResourceType.MANDRINO, bore=15),
        make_resource("t1", ResourceType.TASTATORE, bore=15),
    ]
    reservations = [
        ResourceReservation(resource_id="m1", start_index=0, duration_days=3),
        ResourceReservation(resource_id="t1", start_index=3, duration_days=2),
    ]
    outcome = solve(
        _problem(
            [make_job("j1", [a_free], due_date=FAR_DUE)],
            resources,
            reservations=reservations,
        )
    )
    assert outcome.feasible
    # m1 busy [0,3), t1 busy [3,5): first index where both are free is 5.
    assert outcome.activity_starts["a1"] >= 5
