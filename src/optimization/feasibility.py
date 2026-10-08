"""Pre-solver feasibility and consistency checks.

Detects instances that are *hard-infeasible* (no valid schedule can exist)
before the CP-SAT model is built, so the solver is only invoked on instances
that admit a schedule. Note: a job being unavoidably *late* is not infeasible —
the solver still returns a valid (penalised) schedule for it.
"""

from __future__ import annotations

from pydantic import BaseModel

from src.optimization.calendar import WorkingCalendar
from src.optimization.compatibility import get_compatible_resources
from src.optimization.data_model import (
    Activity,
    Job,
    Requirement,
    Resource,
    SchedulingProblem,
    precedence_pairs,
    topological_activities,
)
from src.optimization.requirements import generate_requirements


class FeasibilityIssue(BaseModel):
    """A single reason an instance cannot produce any valid schedule."""

    kind: str
    activity_id: str | None = None
    job_id: str | None = None
    detail: str


class FeasibilityReport(BaseModel):
    """Outcome of the pre-solver feasibility analysis."""

    feasible: bool
    issues: list[FeasibilityIssue]


def _resource_shortage_issue(
    activity: Activity,
    requirement: Requirement,
    available: int,
) -> FeasibilityIssue:
    return FeasibilityIssue(
        kind="insufficient_resources",
        activity_id=activity.activity_id,
        job_id=activity.job_id,
        detail=(
            f"Requirement for {requirement.quantity} x "
            f"{requirement.resource_type.value} has only {available} compatible "
            f"resource(s) in inventory."
        ),
    )


def _check_resource_coverage(
    activity: Activity,
    resources: list[Resource],
    compatibility: dict[str, list[str]],
    issues: list[FeasibilityIssue],
) -> None:
    """Each requirement must have at least ``quantity`` compatible resources."""
    for requirement in generate_requirements(activity):
        compatible = get_compatible_resources(resources, requirement, compatibility)
        if len(compatible) < requirement.quantity:
            issues.append(
                _resource_shortage_issue(activity, requirement, len(compatible))
            )


def earliest_completion_index(
    job: Job,
    calendar: WorkingCalendar,
    *,
    honor_earliest_start: bool = True,
) -> int:
    """Earliest exclusive completion index of a job, ignoring resources.

    Longest path over the job's precedence DAG: each activity starts at the
    maximum of its predecessors' ends (and optionally its ``earliest_start``
    lower bound); the job completes at the maximum activity end.
    """
    predecessors: dict[str, list[Activity]] = {
        a.activity_id: [] for a in job.activities
    }
    for prev, curr in precedence_pairs(job):
        predecessors[curr.activity_id].append(prev)

    end: dict[str, int] = {}
    for activity in topological_activities(job):
        lower_bound = max(
            (end[p.activity_id] for p in predecessors[activity.activity_id]),
            default=0,
        )
        if honor_earliest_start and activity.earliest_start is not None:
            lower_bound = max(
                lower_bound,
                calendar.date_to_working_index(activity.earliest_start),
            )
        end[activity.activity_id] = lower_bound + activity.duration_days
    return max(end.values())


def _check_horizon(
    problem: SchedulingProblem,
    calendar: WorkingCalendar,
    issues: list[FeasibilityIssue],
) -> None:
    """Each job must fit within the horizon after its earliest starts.

    The earliest a job can finish is the longest path through its precedence
    graph including ``earliest_start`` lower bounds. If that minimum finish
    exceeds the horizon the instance cannot be scheduled at all.
    """
    horizon = problem.horizon_days
    assert horizon is not None  # set by SchedulingProblem.derive_horizon
    for job in problem.jobs:
        earliest_end = earliest_completion_index(job, calendar)
        if earliest_end > horizon:
            issues.append(
                FeasibilityIssue(
                    kind="horizon_too_short",
                    job_id=job.job_id,
                    detail=(
                        f"Job earliest finish (working index {earliest_end}) "
                        f"exceeds horizon ({horizon})."
                    ),
                )
            )


def check_feasibility(problem: SchedulingProblem) -> FeasibilityReport:
    """Run all pre-solver checks and return a structured report."""
    issues: list[FeasibilityIssue] = []
    calendar = WorkingCalendar(problem.horizon_start)

    for job in problem.jobs:
        for activity in job.activities:
            _check_resource_coverage(
                activity, problem.resources, problem.generator_compatibility, issues
            )

    _check_horizon(problem, calendar, issues)

    return FeasibilityReport(feasible=not issues, issues=issues)
