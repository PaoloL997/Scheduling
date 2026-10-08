"""Domain data models for the scheduling engine.

Activities carry their requirement *attributes* directly (bore range, tube
pitch, head model) plus the required quantity per resource type. The concrete
``Requirement`` objects are *generated* from those attributes in
``requirements.py``.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from src.optimization.calendar import WorkingCalendar


class ResourceType(str, Enum):
    MANDRINO = "mandrino"
    TASTATORE = "tastatore"
    MASCHERA = "maschera"
    TESTA = "testa"
    GENERATORE = "generatore"


#: Resource types implied by the presence of a bore range on an activity.
BORE_RESOURCE_TYPES = (ResourceType.MANDRINO, ResourceType.TASTATORE)
#: Resource types implied by the presence of a head model on an activity.
HEAD_RESOURCE_TYPES = (ResourceType.TESTA, ResourceType.GENERATORE)


class Resource(BaseModel):
    """A unit-capacity physical resource (one activity at a time)."""

    resource_id: str
    resource_type: ResourceType
    #: Plant name as written in the application; descriptive only.
    plant: str

    # Optional attributes (which one is relevant depends on resource_type).
    bore: float | None = None
    tube_pitch: float | None = None
    model: str | None = None

    @model_validator(mode="after")
    def check_bore(self):
        if self.bore is not None and self.bore <= 0:
            raise ValueError("Bore must be a positive number.")
        return self

    @model_validator(mode="after")
    def check_tube_pitch(self):
        if self.tube_pitch is not None and self.tube_pitch <= 0:
            raise ValueError("Tube pitch must be a positive number.")
        return self


class Requirement(BaseModel):
    """A generated request for ``quantity`` resources of a given type.

    Created from an activity's attributes by
    ``requirements.generate_requirements``; not provided directly in the input.
    """

    resource_type: ResourceType
    quantity: int = Field(default=1, ge=1)

    bore_min: float | None = None
    bore_mid: float | None = None
    bore_max: float | None = None
    tube_pitch: float | None = None
    head_model: str | None = None

    @model_validator(mode="after")
    def check_bore(self):
        if self.bore_min is not None and self.bore_max is not None:
            if self.bore_min > self.bore_max:
                raise ValueError("bore_min cannot be greater than bore_max.")
            if self.bore_mid is not None and not (
                self.bore_min <= self.bore_mid <= self.bore_max
            ):
                raise ValueError("bore_mid must be between bore_min and bore_max.")
        return self


class Activity(BaseModel):
    """A single workpiece operation belonging to a job."""

    activity_id: str
    job_id: str
    order: int
    plant: str  # descriptive only, never a temporal constraint
    duration_days: int  # working days
    earliest_start: date | None = None
    #: Explicit precedence: ids of same-job activities that must finish first.
    #: ``None`` means "unspecified" — the job falls back to a linear chain by
    #: ``order`` (legacy behaviour). An empty list means "no predecessors".
    predecessors: list[str] | None = None

    # Requirement-generating attributes (present only when applicable).
    bore_min: float | None = None
    bore_mid: float | None = None
    bore_max: float | None = None
    tube_pitch: float | None = None
    head_model: str | None = None

    #: Required quantity per resource type; missing types default to 1 when the
    #: corresponding attribute implies that resource type.
    required_quantities: dict[ResourceType, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_duration(self):
        if self.duration_days <= 0:
            raise ValueError("Duration must be a positive integer.")
        return self

    @model_validator(mode="after")
    def check_bore(self):
        bore_values = (self.bore_min, self.bore_mid, self.bore_max)
        if any(v is not None for v in bore_values):
            if self.bore_min is None or self.bore_max is None:
                raise ValueError(
                    "bore_min and bore_max are both required when a bore range is set."
                )
            if self.bore_min > self.bore_max:
                raise ValueError("bore_min cannot be greater than bore_max.")
            if self.bore_mid is not None and not (
                self.bore_min <= self.bore_mid <= self.bore_max
            ):
                raise ValueError("bore_mid must be between bore_min and bore_max.")
        return self

    @model_validator(mode="after")
    def check_required_quantities(self):
        for qty in self.required_quantities.values():
            if qty < 0:
                raise ValueError("Required quantities must be >= 0.")
        return self


class ResourceReservation(BaseModel):
    """A resource blocked by an activity outside the solved job set.

    Activities already started or completed are never part of the model;
    they only appear here, as immovable bookings on their resources.
    """

    resource_id: str
    start_index: int = Field(ge=0)
    duration_days: int = Field(ge=1)
    activity_id: str | None = None


class Job(BaseModel):
    """A commission: one or more activities linked by precedence constraints.

    When no activity declares ``predecessors`` the job behaves as a linear
    chain ordered by ``Activity.order``; otherwise the explicit precedence
    graph (a DAG) is used and unrelated activities may run in parallel.
    """

    job_id: str
    due_date: date
    penalty_per_week: float = Field(ge=0)
    activities: list[Activity]

    @model_validator(mode="after")
    def check_activities(self):
        if not self.activities:
            raise ValueError("A job must contain at least one activity.")
        for activity in self.activities:
            if activity.job_id != self.job_id:
                raise ValueError(
                    f"Activity {activity.activity_id} has job_id "
                    f"{activity.job_id}, expected {self.job_id}."
                )
        orders = [activity.order for activity in self.activities]
        if orders != sorted(orders):
            raise ValueError("Activity orders must be in ascending order.")
        if sorted(orders) != list(range(1, len(self.activities) + 1)):
            raise ValueError(
                "Activity orders must be a continuous sequence starting from 1."
            )
        return self

    @model_validator(mode="after")
    def check_predecessors(self):
        ids = {activity.activity_id for activity in self.activities}
        for activity in self.activities:
            for pred_id in activity.predecessors or []:
                if pred_id == activity.activity_id:
                    raise ValueError(
                        f"Activity {activity.activity_id} cannot be its own "
                        "predecessor."
                    )
                if pred_id not in ids:
                    raise ValueError(
                        f"Activity {activity.activity_id} references unknown "
                        f"predecessor {pred_id}."
                    )
        # Raises ValueError on a precedence cycle.
        topological_activities(self)
        return self


def precedence_pairs(job: Job) -> list[tuple[Activity, Activity]]:
    """Return the (predecessor, successor) pairs of a job.

    Falls back to the legacy linear chain by ``order`` when no activity
    declares an explicit ``predecessors`` list.
    """
    if all(activity.predecessors is None for activity in job.activities):
        ordered = sorted(job.activities, key=lambda a: a.order)
        return list(zip(ordered, ordered[1:]))

    by_id = {activity.activity_id: activity for activity in job.activities}
    pairs: list[tuple[Activity, Activity]] = []
    for activity in sorted(job.activities, key=lambda a: a.order):
        for pred_id in activity.predecessors or []:
            pairs.append((by_id[pred_id], activity))
    return pairs


def topological_activities(job: Job) -> list[Activity]:
    """Activities in a precedence-respecting order (Kahn's algorithm).

    Ties are broken by ``Activity.order``. Raises ``ValueError`` when the
    precedence graph contains a cycle.
    """
    successors: dict[str, list[Activity]] = {a.activity_id: [] for a in job.activities}
    in_degree: dict[str, int] = {a.activity_id: 0 for a in job.activities}
    for prev, curr in precedence_pairs(job):
        successors[prev.activity_id].append(curr)
        in_degree[curr.activity_id] += 1

    ready = sorted(
        (a for a in job.activities if in_degree[a.activity_id] == 0),
        key=lambda a: a.order,
    )
    result: list[Activity] = []
    while ready:
        activity = ready.pop(0)
        result.append(activity)
        for succ in successors[activity.activity_id]:
            in_degree[succ.activity_id] -= 1
            if in_degree[succ.activity_id] == 0:
                ready.append(succ)
        ready.sort(key=lambda a: a.order)

    if len(result) != len(job.activities):
        raise ValueError(f"Job {job.job_id} has a cycle in its activity precedences.")
    return result


class SchedulingProblem(BaseModel):
    """A full problem instance: jobs, resource inventory and planning horizon."""

    jobs: list[Job]
    resources: list[Resource]
    horizon_start: date
    #: Horizon length in working days; auto-derived (safe upper bound) when None.
    horizon_days: int | None = None
    #: Resource intervals blocked by fixed activities of commesse not in ``jobs``.
    resource_reservations: list[ResourceReservation] = Field(default_factory=list)
    #: Generator models compatible with each head model.  A head model that
    #: is missing here accepts any generator (see ``compatibility``).
    generator_compatibility: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_unique_ids(self):
        job_ids = [job.job_id for job in self.jobs]
        if len(job_ids) != len(set(job_ids)):
            raise ValueError("Job ids must be unique.")
        activity_ids = [a.activity_id for job in self.jobs for a in job.activities]
        if len(activity_ids) != len(set(activity_ids)):
            raise ValueError("Activity ids must be unique across all jobs.")
        resource_ids = [r.resource_id for r in self.resources]
        if len(resource_ids) != len(set(resource_ids)):
            raise ValueError("Resource ids must be unique.")
        return self

    @model_validator(mode="after")
    def derive_horizon(self):
        if self.horizon_days is None:
            self.horizon_days = self._safe_horizon_upper_bound()
        elif self.horizon_days <= 0:
            raise ValueError("horizon_days must be a positive integer.")
        return self

    def _safe_horizon_upper_bound(self) -> int:
        """Upper bound: latest earliest-start index + sum of all durations.

        This trivially admits a fully sequential schedule placed after the
        latest binding ``earliest_start``, so a feasible packing always fits.
        """
        calendar = WorkingCalendar(self.horizon_start)
        latest_earliest = 0
        total_duration = 0
        for job in self.jobs:
            for activity in job.activities:
                total_duration += activity.duration_days
                if activity.earliest_start is not None:
                    latest_earliest = max(
                        latest_earliest,
                        calendar.date_to_working_index(activity.earliest_start),
                    )
        reserved = sum(r.duration_days for r in self.resource_reservations)
        return latest_earliest + total_duration + reserved
