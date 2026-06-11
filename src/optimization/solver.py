"""Public entry point for the scheduling engine.

Typical usage::

    from src.optimization.solver import schedule

    result, diagnoses = schedule(problem)
"""

from __future__ import annotations

from src.optimization.cp_scheduler import SolveOutcome, solve
from src.optimization.data_model import SchedulingProblem
from src.optimization.diagnostics import JobDiagnosis, diagnose
from src.optimization.output_model import ScheduleResult, build_schedule_result

__all__ = [
    "solve",
    "SolveOutcome",
    "schedule",
    "ScheduleResult",
    "JobDiagnosis",
]


def schedule(
    problem: SchedulingProblem,
    time_limit: float | None = None,
) -> tuple[ScheduleResult | None, list[JobDiagnosis]]:
    """Solve ``problem`` and return ``(schedule_result, diagnoses)``.

    When the instance is infeasible (failed pre-solver check or no solution),
    ``schedule_result`` is ``None`` and ``diagnoses`` is empty.
    """
    outcome = solve(problem, time_limit=time_limit)
    if not outcome.feasible:
        return None, []

    result = build_schedule_result(problem, outcome)
    diagnoses = diagnose(problem, outcome)
    return result, diagnoses
