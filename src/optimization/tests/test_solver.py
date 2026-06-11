"""Tests for the public solver facade."""

from __future__ import annotations

from datetime import date

from src.optimization.data_model import SchedulingProblem
from src.optimization.solver import schedule
from src.optimization.tests.builders import make_activity, make_job


def _problem(jobs, resources, horizon_days=None):
    return SchedulingProblem(
        jobs=jobs,
        resources=resources,
        horizon_start=date(2026, 1, 1),
        horizon_days=horizon_days,
    )


def test_schedule_returns_result_and_diagnoses():
    a1 = make_activity("a1", "j1", 1, duration_days=1)
    problem = _problem([make_job("j1", [a1], due_date=date(2030, 1, 1))], [])
    result, diagnoses = schedule(problem)
    assert result is not None
    assert result.total_penalty == 0.0
    assert len(diagnoses) == 1
    assert not diagnoses[0].late


def test_schedule_infeasible_returns_none():
    a1 = make_activity("a1", "j1", 1, duration_days=10)
    problem = _problem(
        [make_job("j1", [a1], due_date=date(2030, 1, 1))], [], horizon_days=3
    )
    result, diagnoses = schedule(problem)
    assert result is None
    assert diagnoses == []
