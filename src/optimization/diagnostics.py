"""Tardiness diagnostics.

For every late job, classify the dominant cause of the delay so a planner can
act on it. Causes (in priority order):

* ``chain_too_long``  -- even placed at index 0 with infinite resources the job
  cannot meet its due date (its precedence critical path is too long).
* ``earliest_start``  -- the precedence path alone would meet the due date, but
  a binding ``earliest_start`` pushes it past it.
* ``resource_contention`` -- the job could finish on time in isolation, but in
  the full solution it had to wait for shared resources.
"""

from __future__ import annotations

from pydantic import BaseModel

from src.optimization.calendar import WorkingCalendar
from src.optimization.cp_scheduler import SolveOutcome
from src.optimization.data_model import Job, SchedulingProblem
from src.optimization.feasibility import earliest_completion_index


class JobDiagnosis(BaseModel):
    """Diagnosis for a single job."""

    job_id: str
    late: bool
    delay_days: int
    primary_cause: str | None = None
    detail: str | None = None


def _classify(
    job: Job,
    calendar: WorkingCalendar,
) -> tuple[str, str]:
    solo_end = earliest_completion_index(job, calendar, honor_earliest_start=True)
    solo_date = calendar.working_index_to_date(solo_end - 1)
    if solo_date <= job.due_date:
        return (
            "resource_contention",
            "Job could meet its due date in isolation; it waited for shared resources.",
        )

    zero_end = earliest_completion_index(job, calendar, honor_earliest_start=False)
    zero_date = calendar.working_index_to_date(zero_end - 1)
    if zero_date <= job.due_date:
        return (
            "earliest_start",
            "A binding earliest_start prevents the chain from finishing on time.",
        )

    return (
        "chain_too_long",
        "The total activity duration exceeds the time available before the due date.",
    )


def diagnose(
    problem: SchedulingProblem,
    outcome: SolveOutcome,
) -> list[JobDiagnosis]:
    """Diagnose each job; late jobs carry a primary cause and detail."""
    if not outcome.feasible:
        raise ValueError("Cannot diagnose an infeasible outcome.")

    calendar = WorkingCalendar(problem.horizon_start)
    diagnoses: list[JobDiagnosis] = []

    for job in problem.jobs:
        completion = max(
            calendar.completion_date(
                outcome.activity_starts[a.activity_id], a.duration_days
            )
            for a in job.activities
        )
        delay_days = calendar.calendar_delay_days(completion, job.due_date)

        if delay_days <= 0:
            diagnoses.append(JobDiagnosis(job_id=job.job_id, late=False, delay_days=0))
            continue

        cause, detail = _classify(job, calendar)
        diagnoses.append(
            JobDiagnosis(
                job_id=job.job_id,
                late=True,
                delay_days=delay_days,
                primary_cause=cause,
                detail=detail,
            )
        )

    return diagnoses
