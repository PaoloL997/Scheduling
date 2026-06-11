"""Generate concrete resource requirements from an activity's attributes.

Rules (driven purely by which attributes are present on the activity):

* ``bore_min``/``bore_max`` present  -> requires **mandrino** and **tastatore**;
  ``bore_mid`` is a soft preference.
* ``tube_pitch`` present             -> requires **maschera** (exact match).
* ``head_model`` present             -> requires **testa** and **generatore**
  (head and generator must agree on the same model).

The required quantity for each implied type comes from
``activity.required_quantities`` (default 1).
"""

from __future__ import annotations

from src.optimization.data_model import (
    BORE_RESOURCE_TYPES,
    HEAD_RESOURCE_TYPES,
    Activity,
    Requirement,
    ResourceType,
)


def _quantity_for(activity: Activity, resource_type: ResourceType) -> int:
    """Required quantity for ``resource_type`` on ``activity`` (default 1)."""
    return activity.required_quantities.get(resource_type, 1)


def generate_requirements(activity: Activity) -> list[Requirement]:
    """Derive the list of ``Requirement`` objects implied by ``activity``."""
    requirements: list[Requirement] = []

    if activity.bore_min is not None and activity.bore_max is not None:
        for resource_type in BORE_RESOURCE_TYPES:
            requirements.append(
                Requirement(
                    resource_type=resource_type,
                    quantity=_quantity_for(activity, resource_type),
                    bore_min=activity.bore_min,
                    bore_mid=activity.bore_mid,
                    bore_max=activity.bore_max,
                )
            )

    if activity.tube_pitch is not None:
        requirements.append(
            Requirement(
                resource_type=ResourceType.MASCHERA,
                quantity=_quantity_for(activity, ResourceType.MASCHERA),
                tube_pitch=activity.tube_pitch,
            )
        )

    if activity.head_model is not None:
        for resource_type in HEAD_RESOURCE_TYPES:
            requirements.append(
                Requirement(
                    resource_type=resource_type,
                    quantity=_quantity_for(activity, resource_type),
                    head_model=activity.head_model,
                )
            )

    return requirements
