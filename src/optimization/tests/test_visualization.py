"""Tests for graphical schedule output."""

from __future__ import annotations

from datetime import date

import matplotlib

matplotlib.use("Agg")

from src.optimization.cp_scheduler import solve
from src.optimization.data_model import SchedulingProblem
from src.optimization.output_model import build_schedule_result
from src.optimization.tests.builders import make_activity, make_job
from src.optimization.visualization import plot_schedule_result


def test_plot_schedule_result_returns_figure_and_can_save(tmp_path):
    a1 = make_activity("a1", "j1", 1, duration_days=2)
    a2 = make_activity("a2", "j1", 2, duration_days=1)
    problem = SchedulingProblem(
        jobs=[make_job("j1", [a1, a2], due_date=date(2026, 1, 10))],
        resources=[],
        horizon_start=date(2026, 1, 1),
    )
    outcome = solve(problem)
    result = build_schedule_result(problem, outcome)
    output_path = tmp_path / "schedule.png"

    figure = plot_schedule_result(problem, result, output_path=output_path)

    assert output_path.exists()
    assert output_path.stat().st_size > 0
    assert len(figure.axes) == 1
    assert figure.axes[0].patches
