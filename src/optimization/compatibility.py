"""Resource / requirement compatibility.

One rule, the same the planning picker and the tablet apply
(``ui/services/measures.py``):

* mandrino, tastatore: the resource bore lies in ``[bore_min, bore_max]``;
* maschera: the tube pitch matches (float tolerance);
* testa: the model matches, ignoring case and surrounding blanks;
* generatore: the model is one of those configured for the head model.  A
  head model with **no** configured generators accepts any generator, as the
  picker does, so an unconfigured model never makes the instance infeasible.

The generator mapping belongs to the problem instance
(``SchedulingProblem.generator_compatibility``): nothing here is global.
"""

from __future__ import annotations

import math

from src.optimization.data_model import Requirement, Resource, ResourceType

#: Absolute tolerance for the tube-pitch comparison, as in the picker.
PITCH_TOLERANCE = 1e-6

GeneratorCompatibility = dict[str, list[str]]


def _norm(model: str | None) -> str:
    return (model or "").strip().lower()


def is_compatible(
    resource: Resource,
    requirement: Requirement,
    compatibility: GeneratorCompatibility | None = None,
) -> bool:
    """Return whether *resource* can serve *requirement*."""
    if resource.resource_type != requirement.resource_type:
        return False

    if resource.resource_type in (ResourceType.MANDRINO, ResourceType.TASTATORE):
        if resource.bore is None:
            return False
        if requirement.bore_min is not None and resource.bore < requirement.bore_min:
            return False
        if requirement.bore_max is not None and resource.bore > requirement.bore_max:
            return False
        return True

    if resource.resource_type == ResourceType.MASCHERA:
        return (
            resource.tube_pitch is not None
            and requirement.tube_pitch is not None
            and math.isclose(
                resource.tube_pitch, requirement.tube_pitch, abs_tol=PITCH_TOLERANCE
            )
        )

    if resource.resource_type == ResourceType.TESTA:
        return bool(resource.model) and _norm(resource.model) == _norm(
            requirement.head_model
        )

    if resource.resource_type == ResourceType.GENERATORE:
        allowed = _allowed_generators(compatibility, requirement.head_model)
        if allowed is None:
            return True
        return _norm(resource.model) in allowed

    return False


def _allowed_generators(
    compatibility: GeneratorCompatibility | None, head_model: str | None
) -> set[str] | None:
    """Normalised generator models for *head_model*; ``None`` when unconstrained."""
    if not compatibility:
        return None
    wanted = _norm(head_model)
    for head, models in compatibility.items():
        if _norm(head) == wanted:
            allowed = {_norm(m) for m in models if _norm(m)}
            return allowed or None
    return None


def get_compatible_resources(
    resources: list[Resource],
    requirement: Requirement,
    compatibility: GeneratorCompatibility | None = None,
) -> list[Resource]:
    """Return the resources that can serve *requirement*, in inventory order."""
    return [r for r in resources if is_compatible(r, requirement, compatibility)]
