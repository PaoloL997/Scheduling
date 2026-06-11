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

# TODO: da prendere direttamente dal DB
PLANTS = [
    "Valbrembo",
    "Ricengo",
    "Schio",
    "Marghera",
    "Albignasego",
    "Milano",
]

HEAD_MODELS = [
    "TP60",
    "SPX",
]


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
    plant: str

    # Optional attributes (which one is relevant depends on resource_type).
    bore: float | None = None
    tube_pitch: float | None = None
    model: str | None = None

    @model_validator(mode="after")
    def check_plant(self):
        if self.plant not in PLANTS:
            raise ValueError(f"Invalid plant: {self.plant}. Must be one of {PLANTS}.")
        return self

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
    def check_head_model(self):
        if self.head_model is not None and self.head_model not in HEAD_MODELS:
            raise ValueError(
                f"Invalid head_model: {self.head_model}. Must be one of {HEAD_MODELS}."
            )
        return self

    @model_validator(mode="after")
    def check_required_quantities(self):
        for qty in self.required_quantities.values():
            if qty < 1:
                raise ValueError("Required quantities must be >= 1.")
        return self


class Job(BaseModel):
    """A commission: an ordered chain of one or more activities."""

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


class SchedulingProblem(BaseModel):
    """A full problem instance: jobs, resource inventory and planning horizon."""

    jobs: list[Job]
    resources: list[Resource]
    horizon_start: date
    #: Horizon length in working days; auto-derived (safe upper bound) when None.
    horizon_days: int | None = None

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
        return latest_earliest + total_duration
