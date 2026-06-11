"""Shared pytest builders for optimization tests."""

from __future__ import annotations

from datetime import date

from src.optimization.data_model import (
    Activity,
    Job,
    Resource,
    ResourceType,
)


def make_resource(
    resource_id: str,
    resource_type: ResourceType,
    *,
    plant: str = "Valbrembo",
    bore: float | None = None,
    tube_pitch: float | None = None,
    model: str | None = None,
) -> Resource:
    return Resource(
        resource_id=resource_id,
        resource_type=resource_type,
        plant=plant,
        bore=bore,
        tube_pitch=tube_pitch,
        model=model,
    )


def make_activity(
    activity_id: str,
    job_id: str,
    order: int,
    *,
    duration_days: int = 1,
    plant: str = "Valbrembo",
    earliest_start: date | None = None,
    bore_min: float | None = None,
    bore_mid: float | None = None,
    bore_max: float | None = None,
    tube_pitch: float | None = None,
    head_model: str | None = None,
    required_quantities: dict[ResourceType, int] | None = None,
) -> Activity:
    return Activity(
        activity_id=activity_id,
        job_id=job_id,
        order=order,
        plant=plant,
        duration_days=duration_days,
        earliest_start=earliest_start,
        bore_min=bore_min,
        bore_mid=bore_mid,
        bore_max=bore_max,
        tube_pitch=tube_pitch,
        head_model=head_model,
        required_quantities=required_quantities or {},
    )


def make_job(
    job_id: str,
    activities: list[Activity],
    *,
    due_date: date = date(2026, 1, 1),
    penalty_per_week: float = 100.0,
) -> Job:
    return Job(
        job_id=job_id,
        due_date=due_date,
        penalty_per_week=penalty_per_week,
        activities=activities,
    )
