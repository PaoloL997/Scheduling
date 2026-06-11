"""Validity tests for the CP-SAT scheduling model (Phase 3)."""

from __future__ import annotations

from datetime import date

from src.optimization.cp_scheduler import solve
from src.optimization.data_model import ResourceType, SchedulingProblem
from src.optimization.requirements import generate_requirements
from src.optimization.tests.builders import (
    make_activity,
    make_job,
    make_resource,
)

FAR_DUE = date(2030, 1, 1)


def _problem(jobs, resources, horizon_days=None):
    return SchedulingProblem(
        jobs=jobs,
        resources=resources,
        horizon_start=date(2026, 1, 1),
        horizon_days=horizon_days,
    )


def test_precedence_respected():
    a1 = make_activity("a1", "j1", 1, duration_days=2)
    a2 = make_activity("a2", "j1", 2, duration_days=3)
    outcome = solve(_problem([make_job("j1", [a1, a2], due_date=FAR_DUE)], []))
    assert outcome.feasible
    # end(a1) <= start(a2)
    assert outcome.activity_starts["a1"] + 2 <= outcome.activity_starts["a2"]


def test_earliest_start_respected():
    a1 = make_activity("a1", "j1", 1, duration_days=1, earliest_start=date(2026, 1, 8))
    outcome = solve(_problem([make_job("j1", [a1], due_date=FAR_DUE)], []))
    assert outcome.feasible
    assert outcome.activity_starts["a1"] >= 5  # 2026-01-08 is working index 5


def test_exact_resource_coverage():
    a1 = make_activity("a1", "j1", 1, bore_min=10, bore_max=20)
    resources = [
        make_resource("m1", ResourceType.MANDRINO, bore=15),
        make_resource("t1", ResourceType.TASTATORE, bore=15),
    ]
    outcome = solve(_problem([make_job("j1", [a1], due_date=FAR_DUE)], resources))
    assert outcome.feasible
    assigned = set(outcome.activity_resources["a1"])
    assert assigned == {"m1", "t1"}


def test_shared_scarce_resource_forces_sequencing():
    a1 = make_activity("a1", "j1", 1, duration_days=2, bore_min=10, bore_max=20)
    a2 = make_activity("a2", "j2", 1, duration_days=2, bore_min=10, bore_max=20)
    resources = [
        make_resource("m1", ResourceType.MANDRINO, bore=15),
        make_resource("t1", ResourceType.TASTATORE, bore=15),
    ]
    jobs = [
        make_job("j1", [a1], due_date=FAR_DUE),
        make_job("j2", [a2], due_date=FAR_DUE),
    ]
    outcome = solve(_problem(jobs, resources))
    assert outcome.feasible
    s1 = outcome.activity_starts["a1"]
    s2 = outcome.activity_starts["a2"]
    # The two activities share the single mandrino/tastatore -> no overlap.
    assert s1 + 2 <= s2 or s2 + 2 <= s1


def test_quantity_greater_than_one_picks_distinct_resources():
    a1 = make_activity(
        "a1",
        "j1",
        1,
        bore_min=10,
        bore_max=20,
        required_quantities={ResourceType.MANDRINO: 2},
    )
    resources = [
        make_resource("m1", ResourceType.MANDRINO, bore=15),
        make_resource("m2", ResourceType.MANDRINO, bore=12),
        make_resource("t1", ResourceType.TASTATORE, bore=15),
    ]
    outcome = solve(_problem([make_job("j1", [a1], due_date=FAR_DUE)], resources))
    assert outcome.feasible
    mandrini = {r for r in outcome.activity_resources["a1"] if r.startswith("m")}
    assert mandrini == {"m1", "m2"}


def test_infeasible_precheck_short_circuits():
    a1 = make_activity("a1", "j1", 1, duration_days=10)
    outcome = solve(
        _problem([make_job("j1", [a1], due_date=FAR_DUE)], [], horizon_days=5)
    )
    assert not outcome.feasible
    assert outcome.status == "INFEASIBLE_PRECHECK"
    assert outcome.feasibility_report is not None


def test_head_model_requires_testa_and_generatore():
    a1 = make_activity("a1", "j1", 1, head_model="TP60")
    resources = [
        make_resource("h1", ResourceType.TESTA, model="TP60"),
        make_resource("g1", ResourceType.GENERATORE, model="P6"),
    ]
    # Sanity: requirements derive both types.
    assert {r.resource_type for r in generate_requirements(a1)} == {
        ResourceType.TESTA,
        ResourceType.GENERATORE,
    }
    outcome = solve(_problem([make_job("j1", [a1], due_date=FAR_DUE)], resources))
    assert outcome.feasible
    assert set(outcome.activity_resources["a1"]) == {"h1", "g1"}
