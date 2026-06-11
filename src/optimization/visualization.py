"""Plot helpers for solved scheduling instances."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Literal

from src.optimization.calendar import WorkingCalendar
from src.optimization.data_model import SchedulingProblem
from src.optimization.output_model import ScheduleResult

GroupMode = Literal["job", "plant"]


def _activity_label(
    activity_id: str, resources: list[str], *, max_resources: int
) -> str:
    if not resources:
        return activity_id
    shown = resources[:max_resources]
    suffix = (
        "" if len(resources) <= max_resources else f" +{len(resources) - max_resources}"
    )
    return f"{activity_id}\n{', '.join(shown)}{suffix}"


def _grouped_activity_rows(
    result: ScheduleResult,
    group_by: GroupMode,
) -> list[tuple[str, list]]:
    groups = defaultdict(list)
    for activity in sorted(
        result.activities,
        key=lambda item: (
            item.job_id,
            item.start_date,
            item.end_date,
            item.activity_id,
        ),
    ):
        key = activity.job_id if group_by == "job" else activity.plant
        groups[key].append(activity)
    return sorted(groups.items(), key=lambda item: item[0])


def plot_schedule_result(
    problem: SchedulingProblem,
    result: ScheduleResult,
    *,
    group_by: GroupMode = "job",
    show_due_dates: bool = True,
    show_resource_labels: bool = True,
    max_resources_in_label: int = 3,
    output_path: str | Path | None = None,
    ax=None,
):
    """Plot a Gantt-style chart for ``result`` across ``problem``'s horizon.

    Returns the Matplotlib ``Figure``. Pass ``output_path`` to save the chart
    without needing to call ``figure.savefig(...)`` yourself.
    """
    if problem.horizon_days is None:
        raise ValueError("problem.horizon_days must be set before plotting.")
    if group_by not in ("job", "plant"):
        raise ValueError("group_by must be either 'job' or 'plant'.")

    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    grouped_rows = _grouped_activity_rows(result, group_by)
    if ax is None:
        height = max(4.0, 1.2 + len(result.activities) * 0.45)
        _, ax = plt.subplots(figsize=(14, height), constrained_layout=True)
    figure = ax.figure

    calendar = WorkingCalendar(problem.horizon_start)
    horizon_start = calendar.working_index_to_date(0)
    horizon_end = calendar.working_index_to_date(problem.horizon_days - 1)

    palette = plt.get_cmap("tab20")
    job_colors = {
        job_id: palette(index % palette.N)
        for index, job_id in enumerate(sorted({a.job_id for a in result.activities}))
    }

    y_positions: list[float] = []
    y_labels: list[str] = []
    current_y = 0
    group_boundaries: list[float] = []

    for group_name, activities in grouped_rows:
        if y_positions:
            group_boundaries.append(current_y - 0.5)
            current_y += 0.35
        group_start_y = current_y
        for activity in activities:
            start = mdates.date2num(activity.start_date)
            # Bars are inclusive of the activity completion date.
            width = (activity.end_date - activity.start_date).days + 1
            color = job_colors[activity.job_id]
            ax.barh(
                current_y,
                width,
                left=start,
                height=0.58,
                align="center",
                color=color,
                edgecolor="#2f3a4a",
                linewidth=0.8,
            )
            label = activity.activity_id
            if show_resource_labels:
                label = _activity_label(
                    activity.activity_id,
                    activity.assigned_resource_ids,
                    max_resources=max_resources_in_label,
                )
            ax.text(
                start + width / 2,
                current_y,
                label,
                ha="center",
                va="center",
                fontsize=8,
                color="#111827",
                clip_on=True,
            )
            y_positions.append(current_y)
            y_labels.append(f"{activity.job_id} | {activity.plant}")
            current_y += 1
        ax.text(
            mdates.date2num(horizon_start) - 1,
            (group_start_y + current_y - 1) / 2,
            group_name,
            ha="right",
            va="center",
            fontsize=9,
            fontweight="bold",
            color="#374151",
        )

    if show_due_dates:
        for job in result.jobs:
            due = mdates.date2num(job.due_date)
            ax.axvline(due, color="#b91c1c", linewidth=1, linestyle="--", alpha=0.7)
            ax.text(
                due,
                max(y_positions, default=0) + 0.7,
                f"due {job.job_id}",
                rotation=90,
                ha="right",
                va="bottom",
                fontsize=8,
                color="#7f1d1d",
            )

    for boundary in group_boundaries:
        ax.axhline(boundary, color="#d1d5db", linewidth=0.8)

    ax.set_xlim(
        mdates.date2num(horizon_start - timedelta(days=1)),
        mdates.date2num(horizon_end + timedelta(days=1)),
    )
    ax.set_yticks(y_positions)
    ax.set_yticklabels(y_labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_title(
        f"Scheduling horizon: {horizon_start.isoformat()} - {horizon_end.isoformat()}",
        loc="left",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_xlabel("Date")
    ax.grid(axis="x", color="#e5e7eb", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=12))
    ax.xaxis.set_major_formatter(
        mdates.ConciseDateFormatter(ax.xaxis.get_major_locator())
    )

    if output_path is not None:
        figure.savefig(output_path, dpi=150, bbox_inches="tight")

    return figure
