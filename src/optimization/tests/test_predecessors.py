"""Validation and helper tests for explicit activity precedences."""

from __future__ import annotations

import pytest

from src.optimization.data_model import precedence_pairs, topological_activities
from src.optimization.tests.builders import make_activity, make_job


def test_legacy_chain_when_no_predecessors_declared():
    a1 = make_activity("a1", "j1", 1)
    a2 = make_activity("a2", "j1", 2)
    a3 = make_activity("a3", "j1", 3)
    job = make_job("j1", [a1, a2, a3])
    pairs = [(p.activity_id, c.activity_id) for p, c in precedence_pairs(job)]
    assert pairs == [("a1", "a2"), ("a2", "a3")]


def test_explicit_graph_used_when_declared():
    a1 = make_activity("a1", "j1", 1, predecessors=[])
    a2 = make_activity("a2", "j1", 2, predecessors=[])
    a3 = make_activity("a3", "j1", 3, predecessors=["a1", "a2"])
    job = make_job("j1", [a1, a2, a3])
    pairs = {(p.activity_id, c.activity_id) for p, c in precedence_pairs(job)}
    assert pairs == {("a1", "a3"), ("a2", "a3")}


def test_topological_order_respects_precedences():
    a1 = make_activity("a1", "j1", 1, predecessors=["a3"])
    a2 = make_activity("a2", "j1", 2, predecessors=[])
    a3 = make_activity("a3", "j1", 3, predecessors=[])
    job = make_job("j1", [a1, a2, a3])
    ordered = [a.activity_id for a in topological_activities(job)]
    assert ordered.index("a3") < ordered.index("a1")


def test_unknown_predecessor_rejected():
    a1 = make_activity("a1", "j1", 1, predecessors=["ghost"])
    with pytest.raises(ValueError, match="unknown predecessor"):
        make_job("j1", [a1])


def test_self_predecessor_rejected():
    a1 = make_activity("a1", "j1", 1, predecessors=["a1"])
    with pytest.raises(ValueError, match="own"):
        make_job("j1", [a1])


def test_cycle_rejected():
    a1 = make_activity("a1", "j1", 1, predecessors=["a2"])
    a2 = make_activity("a2", "j1", 2, predecessors=["a1"])
    with pytest.raises(ValueError, match="cycle"):
        make_job("j1", [a1, a2])
