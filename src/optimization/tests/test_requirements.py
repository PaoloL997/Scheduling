"""Tests for requirement generation from activity attributes."""

from __future__ import annotations

from src.optimization.data_model import ResourceType
from src.optimization.requirements import generate_requirements
from src.optimization.tests.builders import make_activity


def _types(requirements):
    return {r.resource_type for r in requirements}


def test_no_attributes_yields_no_requirements():
    activity = make_activity("a1", "j1", 1)
    assert generate_requirements(activity) == []


def test_bore_yields_mandrino_and_tastatore():
    activity = make_activity("a1", "j1", 1, bore_min=10, bore_mid=15, bore_max=20)
    reqs = generate_requirements(activity)
    assert _types(reqs) == {ResourceType.MANDRINO, ResourceType.TASTATORE}
    for r in reqs:
        assert (r.bore_min, r.bore_mid, r.bore_max) == (10, 15, 20)


def test_tube_pitch_yields_maschera():
    activity = make_activity("a1", "j1", 1, tube_pitch=12.5)
    reqs = generate_requirements(activity)
    assert _types(reqs) == {ResourceType.MASCHERA}
    assert reqs[0].tube_pitch == 12.5


def test_head_model_yields_testa_and_generatore():
    activity = make_activity("a1", "j1", 1, head_model="TP60")
    reqs = generate_requirements(activity)
    assert _types(reqs) == {ResourceType.TESTA, ResourceType.GENERATORE}
    for r in reqs:
        assert r.head_model == "TP60"


def test_quantities_taken_from_activity():
    activity = make_activity(
        "a1",
        "j1",
        1,
        bore_min=10,
        bore_max=20,
        required_quantities={ResourceType.MANDRINO: 2},
    )
    reqs = generate_requirements(activity)
    by_type = {r.resource_type: r.quantity for r in reqs}
    assert by_type[ResourceType.MANDRINO] == 2
    assert by_type[ResourceType.TASTATORE] == 1  # default


def test_combined_attributes_yield_all_requirements():
    activity = make_activity(
        "a1",
        "j1",
        1,
        bore_min=10,
        bore_max=20,
        tube_pitch=8.0,
        head_model="SPX",
    )
    reqs = generate_requirements(activity)
    assert _types(reqs) == {
        ResourceType.MANDRINO,
        ResourceType.TASTATORE,
        ResourceType.MASCHERA,
        ResourceType.TESTA,
        ResourceType.GENERATORE,
    }
