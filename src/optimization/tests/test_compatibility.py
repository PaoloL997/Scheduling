"""Tests for resource/requirement compatibility."""

from __future__ import annotations

from src.optimization.compatibility import (
    get_compatible_resources,
    is_compatible,
)
from src.optimization.data_model import Requirement, ResourceType
from src.optimization.tests.builders import make_resource


def test_type_mismatch_is_incompatible():
    resource = make_resource("m1", ResourceType.MANDRINO, bore=15)
    req = Requirement(resource_type=ResourceType.TASTATORE, bore_min=10, bore_max=20)
    assert not is_compatible(resource, req)


def test_bore_within_range_is_compatible():
    req = Requirement(resource_type=ResourceType.MANDRINO, bore_min=10, bore_max=20)
    assert is_compatible(make_resource("m1", ResourceType.MANDRINO, bore=15), req)
    assert is_compatible(make_resource("m2", ResourceType.MANDRINO, bore=10), req)
    assert is_compatible(make_resource("m3", ResourceType.MANDRINO, bore=20), req)


def test_bore_outside_range_is_incompatible():
    req = Requirement(resource_type=ResourceType.MANDRINO, bore_min=10, bore_max=20)
    assert not is_compatible(make_resource("m1", ResourceType.MANDRINO, bore=9), req)
    assert not is_compatible(make_resource("m2", ResourceType.MANDRINO, bore=21), req)


def test_maschera_requires_exact_tube_pitch():
    req = Requirement(resource_type=ResourceType.MASCHERA, tube_pitch=12.5)
    assert is_compatible(
        make_resource("x1", ResourceType.MASCHERA, tube_pitch=12.5), req
    )
    assert not is_compatible(
        make_resource("x2", ResourceType.MASCHERA, tube_pitch=12.6), req
    )


def test_testa_matches_head_model():
    req = Requirement(resource_type=ResourceType.TESTA, head_model="TP60")
    assert is_compatible(make_resource("t1", ResourceType.TESTA, model="TP60"), req)
    assert not is_compatible(make_resource("t2", ResourceType.TESTA, model="SPX"), req)


def test_generatore_matches_via_compatibility_map():
    req = Requirement(resource_type=ResourceType.GENERATORE, head_model="TP60")
    assert is_compatible(make_resource("g1", ResourceType.GENERATORE, model="P6"), req)
    assert not is_compatible(
        make_resource("g2", ResourceType.GENERATORE, model="SPX"), req
    )


def test_get_compatible_resources_filters():
    req = Requirement(resource_type=ResourceType.MANDRINO, bore_min=10, bore_max=20)
    resources = [
        make_resource("m1", ResourceType.MANDRINO, bore=15),
        make_resource("m2", ResourceType.MANDRINO, bore=99),
        make_resource("t1", ResourceType.TASTATORE, bore=15),
    ]
    compatible = get_compatible_resources(resources, req)
    assert [r.resource_id for r in compatible] == ["m1"]
