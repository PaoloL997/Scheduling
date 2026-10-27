"""Culture-invariant parsing and comparison of inventory measures.

Inventory measures (``diameter``, ``pitch``) are stored as free text, and
written with the dot (:func:`normalize_measure`) whatever the user typed;
activity requirements arrive as ``Decimal`` or query-string values.  Every
compatibility check in planning and on the tablet goes through this module so
that a value such as ``"12,5"`` reads as ``12.5`` on any locale and an
unparsable value is excluded from the comparison instead of collapsing to
zero.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any


def parse_measure(value: Any) -> float | None:
    """Return *value* as a finite float, or ``None`` when it cannot be read.

    Accepts numbers, :class:`~decimal.Decimal` and strings using either the
    dot or the comma as decimal separator.  Empty, non-numeric and non-finite
    values yield ``None``: a measure that cannot be read must never be treated
    as zero.

    Args:
        value: Raw measure from the ORM, a request or the WPS parser.

    Returns:
        float | None: The parsed measure, or ``None`` when unreadable.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed if math.isfinite(parsed) else None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def normalize_measure(value: Any) -> str | None:
    """Return a measure as text with the dot as decimal separator.

    Inventory measures are stored as free text and shown as they are, so
    ``"20,5"`` and ``"20.5"`` would sit side by side in the same list.  A
    value that reads as a number is rewritten with the dot (``"20,5"`` →
    ``"20.5"``, ``"14.83"`` unchanged); anything else is only stripped, so a
    free-text note is never altered.

    Args:
        value: Raw measure from a request, an Excel cell or the ORM.

    Returns:
        str | None: The normalised text, or ``None`` when empty.
    """
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    if "," in text and parse_measure(text) is not None:
        return text.replace(",", ".")
    return text


def bore_in_range(value: Any, bore_min: Any, bore_max: Any) -> bool:
    """Return whether a diameter lies within ``[bore_min, bore_max]``.

    Args:
        value: Raw diameter of the inventory item.
        bore_min: Lower bound of the requirement.
        bore_max: Upper bound of the requirement.

    Returns:
        bool: ``False`` when any of the three values is unreadable.
    """
    diameter = parse_measure(value)
    low = parse_measure(bore_min)
    high = parse_measure(bore_max)
    if diameter is None or low is None or high is None:
        return False
    return low <= diameter <= high


def pitch_matches(value: Any, tube_pitch: Any, tol: float = 1e-6) -> bool:
    """Return whether a mask pitch equals the required tube pitch.

    Args:
        value: Raw pitch of the inventory item.
        tube_pitch: Required pitch.
        tol: Absolute tolerance for the float comparison.

    Returns:
        bool: ``False`` when either value is unreadable.
    """
    pitch = parse_measure(value)
    required = parse_measure(tube_pitch)
    if pitch is None or required is None:
        return False
    return math.isclose(pitch, required, abs_tol=tol)


def is_resource_compatible(
    tipo: str,
    row: dict,
    *,
    bore_min: Any = None,
    bore_max: Any = None,
    tube_pitch: Any = None,
    modello_testa: str | None = None,
    gen_models: set[str] | None = None,
) -> bool:
    """Return whether an inventory row satisfies the activity requirements.

    This is the single compatibility rule shared by the planning picker and
    the tablet scan: a filter is applied only when its requirement is set, so
    a category without a requirement accepts every item.

    Args:
        tipo: Resource category (``testa``, ``mandrino``, ``tastatore``,
            ``maschera``, ``generatore``).
        row: Inventory row dict (``diameter``, ``pitch``, ``model`` …).
        bore_min: Lower bore bound for mandrini/tastatori.
        bore_max: Upper bore bound for mandrini/tastatori.
        tube_pitch: Required mask pitch.
        modello_testa: Required head model.
        gen_models: Compatible generator models, or empty/``None`` for any.

    Returns:
        bool: ``True`` when the row passes every active filter.
    """
    if tipo in ("mandrino", "tastatore"):
        if bore_min is None or bore_max is None:
            return True
        return bore_in_range(row.get("diameter"), bore_min, bore_max)
    if tipo == "maschera":
        if tube_pitch is None:
            return True
        return pitch_matches(row.get("pitch"), tube_pitch)
    if tipo == "testa":
        if not modello_testa:
            return True
        return (row.get("model") or "").strip().lower() == modello_testa.strip().lower()
    if tipo == "generatore":
        if not gen_models:
            return True
        return (row.get("model") or "") in gen_models
    return True


def has_measure_requirement(
    tipo: str,
    *,
    bore_min: Any = None,
    bore_max: Any = None,
    tube_pitch: Any = None,
) -> bool:
    """Return whether the activity defines a dimensional requirement for *tipo*.

    Args:
        tipo: Accessory category.
        bore_min: Lower bore bound.
        bore_max: Upper bore bound.
        tube_pitch: Required pitch.

    Returns:
        bool: ``True`` when the measure needed to verify *tipo* is set.
    """
    if tipo in ("mandrino", "tastatore"):
        return (
            parse_measure(bore_min) is not None and parse_measure(bore_max) is not None
        )
    if tipo == "maschera":
        return parse_measure(tube_pitch) is not None
    return False
