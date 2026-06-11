"""Typed row dataclasses used as intermediaries during the import pipeline.

Each dataclass mirrors a Django ORM model and is populated by
:mod:`src.data.import` before bulk-inserting into the Django ORM.
"""

from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TaskRow:
    """One row of the ``Tasks`` table."""

    commessa: str
    item: str
    activity: str
    duration_min: Optional[int]
    start: Optional[str]  # ISO date YYYY-MM-DD
    finish: Optional[str]
    actual_start: Optional[str]
    actual_finish: Optional[str]
    baseline_start: Optional[str]
    baseline_finish: Optional[str]
    plant: Optional[str]
    responsible: Optional[str]
    resources: Optional[str]  # semicolon-separated names
    predecessors: Optional[str]
    modello_testa: Optional[str] = None
    bore_min: Optional[float] = None
    bore_mid: Optional[float] = None
    bore_max: Optional[float] = None
    tube_pitch: Optional[float] = None


@dataclass
class JobRow:
    """One row of the ``Jobs`` table."""

    commessa: str
    total_cost: Optional[float]
    penalty: Optional[float]
    delivery_date: Optional[str]  # ISO date YYYY-MM-DD


@dataclass
class TestaRow:
    """One row of the ``Testa`` table."""

    uid: str
    model: Optional[str]
    plant: Optional[str]
    notes: Optional[str]


@dataclass
class MandrinoRow:
    """One row of the ``Mandrino`` table."""

    uid: str
    diameter: Optional[str]
    plant: Optional[str]
    notes: Optional[str]


@dataclass
class TastatoreRow:
    """One row of the ``Tastatore`` table."""

    uid: str
    diameter: Optional[str]
    plant: Optional[str]
    notes: Optional[str]


@dataclass
class MascheraRow:
    """One row of the ``Maschera`` table."""

    uid: str
    pitch: Optional[str]
    plant: Optional[str]
    center_distance: Optional[str]


@dataclass
class GeneratoreRow:
    """One row of the ``Generatore`` table."""

    uid: str
    model: Optional[str]
    plant: Optional[str]
    notes: Optional[str]
