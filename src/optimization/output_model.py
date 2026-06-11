"""User-facing result models and conversion from a raw solver outcome.

KPIs (delays, weeks, penalties) are recomputed from the solved *calendar dates*
rather than read back from solver variables, so the report is always internally
consistent.
"""

from __future__ import annotations

import math
from datetime import date

from pydantic import BaseModel

from src.optimization.calendar import WorkingCalendar
from src.optimization.cp_scheduler import SolveOutcome
from src.optimization.data_model import Activity, Job, SchedulingProblem

_DAYS_PER_WEEK = 7


class ActivitySchedule(BaseModel):
    """Solved placement of a single activity."""

    activity_id: str
    job_id: str
    plant: str
    start_date: date
    end_date: date
    assigned_resource_ids: list[str]


class JobResult(BaseModel):
    """Per-job delivery KPIs."""

    job_id: str
    due_date: date
    completion_date: date
    delay_days: int
    delay_weeks: int
    penalty: float


class ScheduleResult(BaseModel):
    """Full schedule plus global KPIs."""

    activities: list[ActivitySchedule]
    jobs: list[JobResult]
    total_penalty: float
    num_late_jobs: int


def _activity_schedule(
    activity: Activity,
    start_index: int,
    calendar: WorkingCalendar,
    assigned: list[str],
) -> ActivitySchedule:
    return ActivitySchedule(
        activity_id=activity.activity_id,
        job_id=activity.job_id,
        plant=activity.plant,
        start_date=calendar.working_index_to_date(start_index),
        end_date=calendar.completion_date(start_index, activity.duration_days),
        assigned_resource_ids=sorted(assigned),
    )


def _job_result(
    job: Job,
    completion_date: date,
    calendar: WorkingCalendar,
) -> JobResult:
    delay_days = calendar.calendar_delay_days(completion_date, job.due_date)
    delay_weeks = math.ceil(delay_days / _DAYS_PER_WEEK)
    return JobResult(
        job_id=job.job_id,
        due_date=job.due_date,
        completion_date=completion_date,
        delay_days=delay_days,
        delay_weeks=delay_weeks,
        penalty=job.penalty_per_week * delay_weeks,
    )


def build_schedule_result(
    problem: SchedulingProblem,
    outcome: SolveOutcome,
) -> ScheduleResult:
    """Convert a feasible ``SolveOutcome`` into a ``ScheduleResult``."""
    if not outcome.feasible:
        raise ValueError("Cannot build a schedule from an infeasible outcome.")

    calendar = WorkingCalendar(problem.horizon_start)
    activity_schedules: list[ActivitySchedule] = []
    job_results: list[JobResult] = []

    for job in problem.jobs:
        completion_date = problem.horizon_start
        for activity in job.activities:
            start_index = outcome.activity_starts[activity.activity_id]
            schedule = _activity_schedule(
                activity,
                start_index,
                calendar,
                outcome.activity_resources.get(activity.activity_id, []),
            )
            activity_schedules.append(schedule)
            completion_date = max(completion_date, schedule.end_date)
        job_results.append(_job_result(job, completion_date, calendar))

    total_penalty = sum(jr.penalty for jr in job_results)
    num_late_jobs = sum(1 for jr in job_results if jr.delay_days > 0)

    return ScheduleResult(
        activities=activity_schedules,
        jobs=job_results,
        total_penalty=total_penalty,
        num_late_jobs=num_late_jobs,
    )
