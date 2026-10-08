"""CP-SAT scheduling model: build, solve (lexicographic phases), extract.

Time is modelled in working-day indices. Each activity is a fixed-size interval;
each (activity, compatible resource) pair is an optional interval whose presence
literal selects that physical resource. Unit capacity is enforced with
``NoOverlap`` per resource.

The objective is solved lexicographically, each phase constrained not to worsen
the earlier ones:

1. total tardiness penalty (every job counts, a job without penalty weighs one
   cent per late week);
2. total late calendar days, so a job that is late anyway finishes as early as
   possible and jobs without a real penalty are not left to drift;
3. bore-mid tie-breaker;
4. resource continuity along precedence edges;
5. workload levelling (minimum peak of concurrent activities).

A single ``time_limit`` is a budget for the whole run: the primary phase gets
the largest share and every later phase gets its share of what is left, so a
slow primary phase can no longer multiply the limit by the number of phases.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable

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
    PENALTY_SCALE,
    add_job_penalty,
    calendar_due_offset,
    scale_penalty,
)
from src.optimization.requirements import generate_requirements

_OK = (cp_model.OPTIMAL, cp_model.FEASIBLE)

#: Relative share of the remaining time budget per phase, in phase order.
_PHASE_WEIGHTS = (4.0, 1.0, 1.0, 1.0, 1.0)
#: Below this many seconds a phase is skipped: CP-SAT cannot do useful work.
_MIN_PHASE_SECONDS = 0.05


class SolveOutcome(BaseModel):
    """Raw solver result, consumed by ``output_model`` to build the report."""

    status: str
    feasible: bool
    activity_starts: dict[str, int] = {}
    activity_resources: dict[str, list[str]] = {}
    total_penalty_cents: int = 0
    feasibility_report: FeasibilityReport | None = None


class _Budget:
    """Split one wall-clock budget across the lexicographic phases."""

    def __init__(self, time_limit: float | None) -> None:
        self._deadline = None if time_limit is None else time.monotonic() + time_limit
        self._weights = list(_PHASE_WEIGHTS)

    def next_phase_limit(self) -> float | None:
        """Seconds for the next phase, ``None`` when unlimited, ``0`` when spent."""
        weight = self._weights.pop(0) if self._weights else 1.0
        if self._deadline is None:
            return None
        remaining = self.remaining()
        if remaining <= _MIN_PHASE_SECONDS:
            return 0.0
        share = remaining * weight / (weight + sum(self._weights))
        return max(_MIN_PHASE_SECONDS, share)

    def remaining(self) -> float:
        """Seconds left in the budget (``inf`` when unlimited)."""
        if self._deadline is None:
            return float("inf")
        return self._deadline - time.monotonic()


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
        #: ``(objective weight, real weight in cents, late-weeks var)`` per
        #: job: the objective weight is at least one cent so no job is
        #: ignored, the real weight is what the report adds up.
        self._penalty_components: list[tuple[int, int, cp_model.IntVar]] = []
        self._delay_vars: list[cp_model.IntVar] = []
        # One non-optional interval per activity plus the fixed reservation
        # intervals: together they describe the global workload over time.
        self._load_intervals: list[cp_model.IntervalVar] = []
        self._load_peak: cp_model.IntVar | None = None

        self._build()

    # -- model construction ------------------------------------------------

    def _build(self) -> None:
        self._create_activity_intervals()
        self._add_precedences()
        self._add_assignments_and_capacity()
        self._add_continuity_literals()
        self._add_penalties()
        self._add_load_peak()

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
                # The per-resource intervals in _add_activity_assignments are
                # optional (one per candidate resource): the global workload
                # needs exactly one always-present interval per activity.
                self._load_intervals.append(
                    self.model.NewFixedSizeIntervalVar(
                        start,
                        activity.duration_days,
                        f"load_{activity.activity_id}",
                    )
                )

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
            # Reservations are work already in progress: the load peak must
            # reflect them even though they are not re-schedulable.
            self._load_intervals.append(interval)

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
            candidates = get_compatible_resources(
                self.problem.resources,
                requirement,
                self.problem.generator_compatibility,
            )
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
        assign ``R``. The continuity phase maximises the sum of these booleans
        without worsening the earlier objectives.
        """
        for job in self.problem.jobs:
            for prev, curr in precedence_pairs(job):
                prev_ids = {
                    rid for (aid, rid) in self.assign if aid == prev.activity_id
                }
                curr_ids = {
                    rid for (aid, rid) in self.assign if aid == curr.activity_id
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
            # Job completion is the latest end across all its activities;
            # with parallel branches no single activity is guaranteed last.
            end_day_exprs = [
                self.start[a.activity_id] + a.duration_days - 1 for a in job.activities
            ]
            due_offset = calendar_due_offset(job.due_date, self.problem.horizon_start)
            weeks, delay = add_job_penalty(
                self.model,
                job.job_id,
                end_day_exprs,
                offsets,
                due_offset,
                self.horizon,
            )
            self._penalty_components.append(
                (
                    scale_penalty(job.penalty_per_week),
                    round(job.penalty_per_week * PENALTY_SCALE),
                    weeks,
                )
            )
            self._delay_vars.append(delay)

    def _add_load_peak(self) -> None:
        """Bind ``_load_peak`` to the maximum number of concurrent intervals.

        ``AddCumulative`` with unit demands and a variable capacity makes the
        capacity an upper bound on concurrency; minimizing it turns the bound
        into the actual peak.
        """
        if not self._load_intervals:
            return
        self._load_peak = self.model.NewIntVar(
            0, len(self._load_intervals), "load_peak"
        )
        self.model.AddCumulative(
            self._load_intervals,
            [1] * len(self._load_intervals),
            self._load_peak,
        )

    # -- objective expressions --------------------------------------------

    def _total_penalty_expr(self):
        return sum(weight * weeks for weight, _real, weeks in self._penalty_components)

    def _total_delay_expr(self):
        return sum(self._delay_vars)

    def _objective_penalty_value(self, solver: cp_model.CpSolver) -> int:
        return sum(
            weight * solver.Value(weeks)
            for weight, _real, weeks in self._penalty_components
        )

    def _penalty_value(self, solver: cp_model.CpSolver) -> int:
        """Real penalty in cents (jobs without a penalty add nothing)."""
        return sum(
            real * solver.Value(weeks)
            for _weight, real, weeks in self._penalty_components
        )

    # -- solving -----------------------------------------------------------

    def solve(self, time_limit: float | None) -> SolveOutcome:
        solver = cp_model.CpSolver()
        budget = _Budget(time_limit)

        def run_phase(set_objective: Callable[[], None]) -> int | None:
            """Solve one phase within its share of the budget.

            Returns the CP-SAT status of a solved phase, or ``None`` when the
            budget is spent or the phase found no solution; the model keeps
            every constraint added so far, so a later phase can still run.
            """
            limit = budget.next_phase_limit()
            if limit is not None and limit <= 0:
                return None
            if limit is not None:
                solver.parameters.max_time_in_seconds = limit
            set_objective()
            status = solver.Solve(self.model)
            if status not in _OK:
                last_status.append(status)
                return None
            return status

        last_status: list[int] = []

        # Phase 1: minimize the total tardiness penalty.  A feasible schedule
        # matters more than any later refinement: when the first share runs
        # out without one, the whole remaining budget goes to this phase.
        penalty_expr = self._total_penalty_expr()
        status = run_phase(lambda: self.model.Minimize(penalty_expr))
        if (
            status is None
            and last_status
            and last_status[-1] == cp_model.UNKNOWN
            and budget.remaining() > _MIN_PHASE_SECONDS
        ):
            solver.parameters.max_time_in_seconds = budget.remaining()
            retry = solver.Solve(self.model)
            if retry in _OK:
                status = retry
            else:
                last_status.append(retry)
        if status is None:
            name = solver.StatusName(last_status[-1]) if last_status else "UNKNOWN"
            return SolveOutcome(status=name, feasible=False)

        # Snapshot after every successful phase: if a later phase runs out of
        # time without a solution, the last good solution is still returned.
        best = self._extract(solver, status)
        self.model.Add(penalty_expr <= self._objective_penalty_value(solver))

        # Phase 2: minimize the total late days with the same penalty.
        if self._delay_vars:
            delay_expr = self._total_delay_expr()
            phase = run_phase(lambda: self.model.Minimize(delay_expr))
            if phase is not None:
                status = phase
                best = self._extract(solver, status)
                self.model.Add(delay_expr <= solver.Value(delay_expr))

        # Phase 3: bore-mid tie-breaker.
        if self._tie_break_literals:
            bore_expr = sum(self._tie_break_literals)
            phase = run_phase(lambda: self.model.Minimize(bore_expr))
            if phase is not None:
                status = phase
                best = self._extract(solver, status)
                self.model.Add(bore_expr <= solver.Value(bore_expr))

        # Phase 4: maximize resource continuity along precedence edges.
        if self._continuity_literals:
            continuity_expr = sum(self._continuity_literals)
            phase = run_phase(lambda: self.model.Maximize(continuity_expr))
            if phase is not None:
                status = phase
                best = self._extract(solver, status)
                self.model.Add(continuity_expr >= solver.Value(continuity_expr))

        # Phase 5: level the workload by minimizing the peak concurrency.
        if self._load_peak is not None:
            load_peak = self._load_peak
            phase = run_phase(lambda: self.model.Minimize(load_peak))
            if phase is not None:
                status = phase
                best = self._extract(solver, status)

        return best

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
    ``time_limit`` bounds the whole run, all phases included.
    """
    report = check_feasibility(problem)
    if not report.feasible:
        return SolveOutcome(
            status="INFEASIBLE_PRECHECK",
            feasible=False,
            feasibility_report=report,
        )

    return _SchedulingModel(problem).solve(time_limit)
