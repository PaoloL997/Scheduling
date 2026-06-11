"""Objective tests: tardiness penalty and bore-mid tie-breaker (Phase 4)."""

from __future__ import annotations

from datetime import date

from src.optimization.cp_scheduler import solve
from src.optimization.data_model import ResourceType, SchedulingProblem
from src.optimization.objective import scale_penalty
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


def test_late_job_without_penalty_has_zero_penalty():
    a1 = make_activity("a1", "j1", 1, duration_days=1, earliest_start=date(2026, 1, 8))
    job = make_job("j1", [a1], due_date=date(2026, 1, 1), penalty_per_week=0)
    outcome = solve(_problem([job], []))
    assert outcome.feasible
    assert outcome.total_penalty_cents == 0


def test_on_time_job_has_zero_penalty():
    a1 = make_activity("a1", "j1", 1, duration_days=1)
    job = make_job("j1", [a1], due_date=date(2026, 1, 1), penalty_per_week=100)
    outcome = solve(_problem([job], []))
    assert outcome.feasible
    assert outcome.total_penalty_cents == 0


def test_one_late_day_is_one_week():
    # earliest_start forces completion one week after the due date.
    a1 = make_activity("a1", "j1", 1, duration_days=1, earliest_start=date(2026, 1, 8))
    job = make_job("j1", [a1], due_date=date(2026, 1, 1), penalty_per_week=100)
    outcome = solve(_problem([job], []))
    assert outcome.feasible
    # 7 calendar days late -> ceil(7/7) = 1 week -> 100 * 100 cents.
    assert outcome.total_penalty_cents == scale_penalty(100)


def test_eight_late_days_round_up_to_two_weeks():
    a1 = make_activity("a1", "j1", 1, duration_days=1, earliest_start=date(2026, 1, 9))
    # 2026-01-09 (Fri) completion; due 2026-01-01 -> 8 calendar days late.
    job = make_job("j1", [a1], due_date=date(2026, 1, 1), penalty_per_week=50)
    outcome = solve(_problem([job], []))
    assert outcome.feasible
    assert outcome.total_penalty_cents == 2 * scale_penalty(50)


def test_already_late_job_still_scheduled():
    a1 = make_activity("a1", "j1", 1, duration_days=2)
    job = make_job("j1", [a1], due_date=date(2020, 1, 1), penalty_per_week=10)
    outcome = solve(_problem([job], [], horizon_days=10))
    assert outcome.feasible
    assert outcome.total_penalty_cents > 0
    assert "a1" in outcome.activity_starts


def test_bore_mid_preferred_when_penalty_unaffected():
    a1 = make_activity("a1", "j1", 1, bore_min=10, bore_mid=15, bore_max=20)
    resources = [
        make_resource("m_other", ResourceType.MANDRINO, bore=12),
        make_resource("m_mid", ResourceType.MANDRINO, bore=15),
        make_resource("t1", ResourceType.TASTATORE, bore=15),
    ]
    outcome = solve(_problem([make_job("j1", [a1], due_date=FAR_DUE)], resources))
    assert outcome.feasible
    assert outcome.total_penalty_cents == 0
    assert "m_mid" in outcome.activity_resources["a1"]
    assert "m_other" not in outcome.activity_resources["a1"]


def test_tie_break_never_worsens_penalty():
    # Only a non-mid bore resource is available; penalty must stay minimal and
    # the schedule must remain feasible.
    a1 = make_activity("a1", "j1", 1, bore_min=10, bore_mid=15, bore_max=20)
    resources = [
        make_resource("m_other", ResourceType.MANDRINO, bore=12),
        make_resource("t1", ResourceType.TASTATORE, bore=15),
    ]
    outcome = solve(_problem([make_job("j1", [a1], due_date=FAR_DUE)], resources))
    assert outcome.feasible
    assert set(outcome.activity_resources["a1"]) == {"m_other", "t1"}
