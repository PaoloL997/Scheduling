"""Objective helpers: tardiness penalty and bore-mid tie-breaker.

These functions encapsulate the integer encoding of the lateness penalty so the
model builder in ``cp_scheduler.py`` stays focused on structure.
"""

from __future__ import annotations

from datetime import date

from ortools.sat.python import cp_model

#: Penalties are scaled to integer "cents" so CP-SAT can optimize them exactly.
PENALTY_SCALE = 100

_DAYS_PER_WEEK = 7


def scale_penalty(penalty_per_week: float) -> int:
    """Scale a per-week penalty to a strictly-positive integer weight.

    A job without a penalty still weighs one cent per late week: the primary
    objective then never *ignores* a job, it only ranks it below every job
    that has a real penalty.
    """
    return max(1, round(penalty_per_week * PENALTY_SCALE))


def add_job_penalty(
    model: cp_model.CpModel,
    job_id: str,
    end_day_exprs: list,
    calendar_offsets: list[int],
    due_offset: int,
    horizon_days: int,
) -> tuple[cp_model.IntVar, cp_model.IntVar]:
    """Add tardiness variables for a job; return ``(late_weeks, late_days)``.

    ``end_day_exprs`` are the working-day indices of the last working day of
    each activity (``start + duration - 1``); the job completes on their
    maximum. The completion calendar offset is obtained from
    ``calendar_offsets`` indexed by that working-day index. ``late_days`` is
    the calendar delay past the due date; ``late_weeks`` is ``ceil(delay / 7)``,
    enforced as ``7 * weeks >= delay`` and driven to equality by the
    objective's minimization pressure.
    """
    last_day = model.NewIntVar(0, horizon_days - 1, f"last_day_{job_id}")
    model.AddMaxEquality(last_day, end_day_exprs)

    completion = model.NewIntVar(
        min(calendar_offsets), max(calendar_offsets), f"completion_{job_id}"
    )
    model.AddElement(last_day, calendar_offsets, completion)

    max_delay = max(0, max(calendar_offsets) - due_offset)
    delay = model.NewIntVar(0, max_delay, f"delay_{job_id}")
    model.AddMaxEquality(delay, [0, completion - due_offset])

    max_weeks = (max_delay + _DAYS_PER_WEEK - 1) // _DAYS_PER_WEEK
    weeks = model.NewIntVar(0, max_weeks, f"weeks_{job_id}")
    model.Add(_DAYS_PER_WEEK * weeks >= delay)

    return weeks, delay


def calendar_due_offset(due_date: date, horizon_start: date) -> int:
    """Calendar-day offset of ``due_date`` relative to ``horizon_start``."""
    return (due_date - horizon_start).days
