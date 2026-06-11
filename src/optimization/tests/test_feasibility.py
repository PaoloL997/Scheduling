"""Tests for the pre-solver feasibility checks."""

from __future__ import annotations

from datetime import date

from src.optimization.data_model import ResourceType, SchedulingProblem
from src.optimization.feasibility import check_feasibility
from src.optimization.tests.builders import (
    make_activity,
    make_job,
    make_resource,
)


def _problem(jobs, resources, horizon_days=None):
    return SchedulingProblem(
        jobs=jobs,
        resources=resources,
        horizon_start=date(2026, 1, 1),
        horizon_days=horizon_days,
    )


def test_feasible_instance():
    activity = make_activity("a1", "j1", 1, bore_min=10, bore_max=20)
    resources = [
        make_resource("m1", ResourceType.MANDRINO, bore=15),
        make_resource("t1", ResourceType.TASTATORE, bore=15),
    ]
    report = check_feasibility(_problem([make_job("j1", [activity])], resources))
    assert report.feasible
    assert report.issues == []


def test_missing_compatible_resource():
    activity = make_activity("a1", "j1", 1, bore_min=10, bore_max=20)
    # No tastatore available at all.
    resources = [make_resource("m1", ResourceType.MANDRINO, bore=15)]
    report = check_feasibility(_problem([make_job("j1", [activity])], resources))
    assert not report.feasible
    kinds = {i.kind for i in report.issues}
    assert "insufficient_resources" in kinds


def test_scarce_resource_below_required_quantity():
    activity = make_activity(
        "a1",
        "j1",
        1,
        bore_min=10,
        bore_max=20,
        required_quantities={ResourceType.MANDRINO: 2},
    )
    resources = [
        make_resource("m1", ResourceType.MANDRINO, bore=15),  # only 1, need 2
        make_resource("t1", ResourceType.TASTATORE, bore=15),
    ]
    report = check_feasibility(_problem([make_job("j1", [activity])], resources))
    assert not report.feasible
    assert any(i.kind == "insufficient_resources" for i in report.issues)


def test_horizon_too_short():
    activity = make_activity("a1", "j1", 1, duration_days=10)
    report = check_feasibility(
        _problem([make_job("j1", [activity])], [], horizon_days=5)
    )
    assert not report.feasible
    assert any(i.kind == "horizon_too_short" for i in report.issues)


def test_binding_earliest_start_pushes_chain_past_horizon():
    activity = make_activity(
        "a1",
        "j1",
        1,
        duration_days=3,
        earliest_start=date(2026, 1, 15),
    )
    report = check_feasibility(
        _problem([make_job("j1", [activity])], [], horizon_days=5)
    )
    assert not report.feasible
    assert any(i.kind == "horizon_too_short" for i in report.issues)


def test_late_job_is_not_infeasible():
    # Due date already in the past, but resources exist and horizon fits.
    activity = make_activity("a1", "j1", 1, duration_days=2)
    job = make_job("j1", [activity], due_date=date(2020, 1, 1))
    report = check_feasibility(_problem([job], [], horizon_days=10))
    assert report.feasible
