"""Tardiness diagnostics.

For every late job, classify the dominant cause of the delay so a planner can
act on it. Causes (in priority order):

* ``chain_too_long``  -- even placed at index 0 with infinite resources the job
  cannot meet its due date (the sum of durations is too large).
* ``earliest_start``  -- the chain alone would meet the due date, but a binding
  ``earliest_start`` pushes it past it.
* ``resource_contention`` -- the job could finish on time in isolation, but in
  the full solution it had to wait for shared resources.
"""

from __future__ import annotations

from pydantic import BaseModel

from src.optimization.calendar import WorkingCalendar
from src.optimization.cp_scheduler import SolveOutcome
from src.optimization.data_model import Job, SchedulingProblem


class JobDiagnosis(BaseModel):
    """Diagnosis for a single job."""

    job_id: str
    late: bool
    delay_days: int
    primary_cause: str | None = None
    detail: str | None = None


def _resource_free_completion(
    job: Job,
    calendar: WorkingCalendar,
    *,
    honor_earliest_start: bool,
) -> int:
    """Earliest exclusive completion index respecting precedence (and optionally
    ``earliest_start``), ignoring resource contention."""
    end = 0
    for activity in sorted(job.activities, key=lambda a: a.order):
        lower = end
        if honor_earliest_start and activity.earliest_start is not None:
            lower = max(lower, calendar.date_to_working_index(activity.earliest_start))
        end = lower + activity.duration_days
    return end


def _classify(
    job: Job,
    calendar: WorkingCalendar,
) -> tuple[str, str]:
    solo_end = _resource_free_completion(job, calendar, honor_earliest_start=True)
    solo_date = calendar.working_index_to_date(solo_end - 1)
    if solo_date <= job.due_date:
        return (
            "resource_contention",
            "Job could meet its due date in isolation; it waited for shared resources.",
        )

    zero_end = _resource_free_completion(job, calendar, honor_earliest_start=False)
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
        last = max(job.activities, key=lambda a: a.order)
        start_index = outcome.activity_starts[last.activity_id]
        completion = calendar.completion_date(start_index, last.duration_days)
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
