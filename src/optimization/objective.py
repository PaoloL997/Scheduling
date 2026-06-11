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
    """Scale a per-week penalty to a strictly-positive integer weight."""
    return max(1, round(penalty_per_week * PENALTY_SCALE))


def add_job_penalty(
    model: cp_model.CpModel,
    job_id: str,
    last_day_expr,
    calendar_offsets: list[int],
    due_offset: int,
    horizon_days: int,
) -> cp_model.IntVar:
    """Add tardiness variables for a job and return its late-weeks var.

    ``last_day_expr`` is the working-day index of the job's last working day
    (``start + duration - 1`` of the last activity). The completion calendar
    offset is obtained from ``calendar_offsets`` indexed by that working-day
    index. The number of late weeks is ``ceil(delay / 7)``, enforced as
    ``7 * weeks >= delay`` and driven to equality by the objective's
    minimization pressure.
    """
    last_day = model.NewIntVar(0, horizon_days - 1, f"last_day_{job_id}")
    model.Add(last_day == last_day_expr)

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

    return weeks


def calendar_due_offset(due_date: date, horizon_start: date) -> int:
    """Calendar-day offset of ``due_date`` relative to ``horizon_start``."""
    return (due_date - horizon_start).days
