from src.optimization.data_model import (
    Resource,
    Requirement,
    ResourceType,
)

# Default fallback used by tests and by callers that do not load compatibility
# from the application database.
GENERATOR_COMPATIBILITY = {
    "TP60": ["P6", "P6-3", "P406", "P6CW", "PC2", "PC350DC"],
    "SPX": ["SPX"],
}


def configure_generator_compatibility(mapping: dict[str, list[str]]) -> None:
    """Replace generator compatibility with application/database values."""
    GENERATOR_COMPATIBILITY.clear()
    GENERATOR_COMPATIBILITY.update(
        {
            head_model: sorted(set(generator_models))
            for head_model, generator_models in mapping.items()
        }
    )


def is_compatible(resource: Resource, requirement: Requirement) -> bool:

    if resource.resource_type != requirement.resource_type:
        return False

    if resource.resource_type in [
        ResourceType.MANDRINO,
        ResourceType.TASTATORE,
    ]:
        if resource.bore is None:
            return False
        if requirement.bore_min is not None:
            if resource.bore < requirement.bore_min:
                return False

        if requirement.bore_max is not None:
            if resource.bore > requirement.bore_max:
                return False
        return True

    if resource.resource_type == ResourceType.MASCHERA:
        return (
            resource.tube_pitch is not None
            and resource.tube_pitch == requirement.tube_pitch
        )

    if resource.resource_type == ResourceType.TESTA:
        return resource.model is not None and resource.model == requirement.head_model

    if resource.resource_type == ResourceType.GENERATORE:
        allowed = GENERATOR_COMPATIBILITY.get(requirement.head_model, [])
        return resource.model in allowed

    return False


def get_compatible_resources(
    resources: list[Resource], requirement: Requirement
) -> list[Resource]:

    compatible = []
    for resource in resources:
        if is_compatible(resource, requirement):
            compatible.append(resource)

    return compatible
