"""CP-SAT scheduling model: build, solve (three-phase lexicographic), extract.

Time is modelled in working-day indices. Each activity is a fixed-size interval;
each (activity, compatible resource) pair is an optional interval whose presence
literal selects that physical resource. Unit capacity is enforced with
``NoOverlap`` per resource. The objective is solved lexicographically: first the
total tardiness penalty, then a bore-mid tie-breaker, then resource continuity
along precedence edges — later phases never worsen earlier objectives.
"""

from __future__ import annotations

from collections import defaultdict

from ortools.sat.python import cp_model
from pydantic import BaseModel

from src.optimization.calendar import WorkingCalendar
from src.optimization.compatibility import get_compatible_resources
from src.optimization.data_model import (
    Activity,
    SchedulingProblem,
    precedence_pairs,
)
from src.optimization.feasibility import FeasibilityReport, check_feasibility
from src.optimization.objective import (
    add_job_penalty,
    calendar_due_offset,
    scale_penalty,
)
from src.optimization.requirements import generate_requirements


class SolveOutcome(BaseModel):
    """Raw solver result, consumed by ``output_model`` to build the report."""

    status: str
    feasible: bool
    activity_starts: dict[str, int] = {}
    activity_resources: dict[str, list[str]] = {}
    total_penalty_cents: int = 0
    feasibility_report: FeasibilityReport | None = None


class _SchedulingModel:
    """Builds the CP-SAT model and holds references to its variables."""

    def __init__(self, problem: SchedulingProblem) -> None:
        self.problem = problem
        self.calendar = WorkingCalendar(problem.horizon_start)
        self.horizon: int = problem.horizon_days  # type: ignore[assignment]
        self.model = cp_model.CpModel()

        self.start: dict[str, cp_model.IntVar] = {}
        self.assign: dict[tuple[str, str], cp_model.IntVar] = {}
        self._tie_break_literals: list[cp_model.IntVar] = []
        self._continuity_literals: list[cp_model.IntVar] = []
        self._penalty_components: list[tuple[int, cp_model.IntVar]] = []

        self._build()

    # -- model construction ------------------------------------------------

    def _build(self) -> None:
        self._create_activity_intervals()
        self._add_precedences()
        self._add_assignments_and_capacity()
        self._add_continuity_literals()
        self._add_penalties()

    def _create_activity_intervals(self) -> None:
        for job in self.problem.jobs:
            for activity in job.activities:
                start = self.model.NewIntVar(
                    0,
                    self.horizon - activity.duration_days,
                    f"start_{activity.activity_id}",
                )
                if activity.earliest_start is not None:
                    lower = self.calendar.date_to_working_index(activity.earliest_start)
                    self.model.Add(start >= lower)
                self.start[activity.activity_id] = start

    def _add_precedences(self) -> None:
        for job in self.problem.jobs:
            for prev, curr in precedence_pairs(job):
                # end(prev) <= start(curr)
                self.model.Add(
                    self.start[prev.activity_id] + prev.duration_days
                    <= self.start[curr.activity_id]
                )

    def _add_assignments_and_capacity(self) -> None:
        resource_intervals: dict[str, list[cp_model.IntervalVar]] = defaultdict(list)
        for job in self.problem.jobs:
            for activity in job.activities:
                self._add_activity_assignments(activity, resource_intervals)

        for reservation in self.problem.resource_reservations:
            interval = self.model.NewFixedSizeIntervalVar(
                reservation.start_index,
                reservation.duration_days,
                f"res_{reservation.activity_id}_{reservation.resource_id}",
            )
            resource_intervals[reservation.resource_id].append(interval)

        for resource_id, intervals in resource_intervals.items():
            if len(intervals) > 1:
                self.model.AddNoOverlap(intervals)

    def _add_activity_assignments(
        self,
        activity: Activity,
        resource_intervals: dict[str, list[cp_model.IntervalVar]],
    ) -> None:
        start = self.start[activity.activity_id]
        for requirement in generate_requirements(activity):
            candidates = get_compatible_resources(self.problem.resources, requirement)
            literals: list[cp_model.IntVar] = []
            for resource in candidates:
                lit = self.model.NewBoolVar(
                    f"x_{activity.activity_id}_{resource.resource_id}"
                )
                self.assign[(activity.activity_id, resource.resource_id)] = lit
                interval = self.model.NewOptionalFixedSizeIntervalVar(
                    start,
                    activity.duration_days,
                    lit,
                    f"oi_{activity.activity_id}_{resource.resource_id}",
                )
                resource_intervals[resource.resource_id].append(interval)
                literals.append(lit)

                if (
                    requirement.bore_mid is not None
                    and resource.bore is not None
                    and resource.bore != requirement.bore_mid
                ):
                    self._tie_break_literals.append(lit)

            self.model.Add(sum(literals) == requirement.quantity)

    def _add_continuity_literals(self) -> None:
        """Soft preference: reuse the same resource across precedence edges.

        For each predecessor→successor pair and each resource ``R`` that is a
        candidate for both activities, introduce a boolean that is true iff both
        assign ``R``. Phase C maximises the sum of these booleans without
        worsening penalty or bore-mid objectives.
        """
        for job in self.problem.jobs:
            for prev, curr in precedence_pairs(job):
                prev_ids = {
                    rid
                    for (aid, rid) in self.assign
                    if aid == prev.activity_id
                }
                curr_ids = {
                    rid
                    for (aid, rid) in self.assign
                    if aid == curr.activity_id
                }
                for resource_id in prev_ids & curr_ids:
                    lit_prev = self.assign[(prev.activity_id, resource_id)]
                    lit_curr = self.assign[(curr.activity_id, resource_id)]
                    same = self.model.NewBoolVar(
                        f"same_{prev.activity_id}_{curr.activity_id}_{resource_id}"
                    )
                    self.model.Add(same <= lit_prev)
                    self.model.Add(same <= lit_curr)
                    self.model.Add(same >= lit_prev + lit_curr - 1)
                    self._continuity_literals.append(same)

    def _add_penalties(self) -> None:
        offsets = self.calendar.calendar_offsets(self.horizon)
        for job in self.problem.jobs:
            if job.penalty_per_week <= 0:
                continue
            # Job completion is the latest end across all its activities;
            # with parallel branches no single activity is guaranteed last.
            end_day_exprs = [
                self.start[a.activity_id] + a.duration_days - 1 for a in job.activities
            ]
            due_offset = calendar_due_offset(job.due_date, self.problem.horizon_start)
            weeks = add_job_penalty(
                self.model,
                job.job_id,
                end_day_exprs,
                offsets,
                due_offset,
                self.horizon,
            )
            self._penalty_components.append(
                (scale_penalty(job.penalty_per_week), weeks)
            )

    # -- objective expressions --------------------------------------------

    def _total_penalty_expr(self):
        return sum(weight * weeks for weight, weeks in self._penalty_components)

    def _penalty_value(self, solver: cp_model.CpSolver) -> int:
        return sum(
            weight * solver.Value(weeks) for weight, weeks in self._penalty_components
        )

    def _tie_break_value(self, solver: cp_model.CpSolver) -> int:
        return sum(solver.Value(lit) for lit in self._tie_break_literals)

    # -- solving -----------------------------------------------------------

    def solve(self, time_limit: float | None) -> SolveOutcome:
        solver = cp_model.CpSolver()
        if time_limit is not None:
            solver.parameters.max_time_in_seconds = time_limit

        # Phase A: minimize the total tardiness penalty.
        self.model.Minimize(self._total_penalty_expr())
        status = solver.Solve(self.model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return SolveOutcome(status=solver.StatusName(status), feasible=False)

        current_penalty = self._penalty_value(solver)
        bore_bound: int | None = None

        # Phase B: only when phase A is provably optimal, minimize the
        # tie-breaker subject to keeping the optimal penalty.
        if status == cp_model.OPTIMAL and self._tie_break_literals:
            self.model.Add(self._total_penalty_expr() <= current_penalty)
            self.model.Minimize(sum(self._tie_break_literals))
            phase_b_status = solver.Solve(self.model)
            if phase_b_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                status = phase_b_status
                current_penalty = self._penalty_value(solver)
                if phase_b_status == cp_model.OPTIMAL:
                    bore_bound = self._tie_break_value(solver)

        # Phase C: maximize resource continuity along precedence edges,
        # without worsening penalty (or bore-mid, when phase B was optimal).
        if self._continuity_literals and status in (
            cp_model.OPTIMAL,
            cp_model.FEASIBLE,
        ):
            self.model.Add(self._total_penalty_expr() <= current_penalty)
            if bore_bound is not None:
                self.model.Add(sum(self._tie_break_literals) <= bore_bound)
            self.model.Maximize(sum(self._continuity_literals))
            phase_c_status = solver.Solve(self.model)
            if phase_c_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                status = phase_c_status

        return self._extract(solver, status)

    def _extract(self, solver: cp_model.CpSolver, status: int) -> SolveOutcome:
        activity_starts = {
            aid: int(solver.Value(start)) for aid, start in self.start.items()
        }
        activity_resources: dict[str, list[str]] = defaultdict(list)
        for (aid, resource_id), lit in self.assign.items():
            if solver.Value(lit):
                activity_resources[aid].append(resource_id)

        return SolveOutcome(
            status=solver.StatusName(status),
            feasible=True,
            activity_starts=activity_starts,
            activity_resources=dict(activity_resources),
            total_penalty_cents=self._penalty_value(solver),
        )


def solve(
    problem: SchedulingProblem,
    time_limit: float | None = None,
) -> SolveOutcome:
    """Feasibility-check then solve a scheduling problem.

    Returns a ``SolveOutcome``; when the pre-solver feasibility check fails the
    outcome is infeasible and carries the diagnostic ``feasibility_report``.
    """
    report = check_feasibility(problem)
    if not report.feasible:
        return SolveOutcome(
            status="INFEASIBLE_PRECHECK",
            feasible=False,
            feasibility_report=report,
        )

    return _SchedulingModel(problem).solve(time_limit)
