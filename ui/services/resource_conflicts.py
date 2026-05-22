"""Resource conflict detection for activity scheduling.

A resource (testa, mandrino, tastatore, maschera, generatore) cannot be
assigned to two activities whose date ranges overlap.  This module provides
the single source of truth for that rule, used by both the wizard save and
the date-update endpoints.

Overlap semantics are inclusive on both ends: an activity that ends on day
``X`` conflicts with one that starts on day ``X`` for the same resource.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ResourceAssignment:
    """A single (tipo, risorsa_id) pair candidate for booking.

    Attributes:
        tipo: Resource category (``testa``, ``mandrino``, ``tastatore``,
            ``maschera``, ``generatore``).
        risorsa_id: Unique identifier of the physical resource.
    """

    tipo: str
    risorsa_id: str


class ResourceConflictError(Exception):
    """Raised when one or more resources are already booked in the given range.

    Attributes:
        conflicts: List of dictionaries describing each conflict, each with
            keys ``tipo``, ``risorsa_id``, ``attivita_id``, ``attivita_label``,
            ``data_inizio``, ``data_fine``.
    """

    def __init__(self, conflicts: list[dict]) -> None:
        """Initialise the exception with a list of conflict descriptors.

        Args:
            conflicts: Conflict descriptors as returned by
                :func:`find_conflicts`.
        """
        super().__init__("Conflitto risorse")
        self.conflicts = conflicts


def find_conflicts(
    assignments: list[ResourceAssignment] | list[tuple[str, str]],
    data_inizio: date,
    data_fine: date,
    exclude_activity_id: int | None = None,
) -> list[dict]:
    """Return existing bookings that overlap the candidate range.

    Iterates the provided assignments and queries :class:`RisorsaAttivita`
    for any row that matches both ``tipo`` and ``risorsa_id`` and whose
    parent activity has a date range overlapping ``[data_inizio, data_fine]``
    inclusively on both ends.

    Args:
        assignments: Candidate resources to book.  May be a list of
            :class:`ResourceAssignment` or ``(tipo, risorsa_id)`` tuples.
        data_inizio: Candidate activity start date.
        data_fine: Candidate activity end date.
        exclude_activity_id: Optional Attivita primary key to ignore (used
            when re-validating an existing activity during an edit flow).

    Returns:
        list[dict]: One dictionary per conflicting booking with keys
            ``tipo``, ``risorsa_id``, ``attivita_id``, ``attivita_label``,
            ``data_inizio``, ``data_fine``.  Empty list when no conflict.
    """
    from ui.models import RisorsaAttivita

    if not assignments:
        return []

    pairs = [
        (a.tipo, a.risorsa_id) if isinstance(a, ResourceAssignment) else tuple(a)
        for a in assignments
    ]

    from django.db.models import Q

    pair_q = Q()
    for tipo, risorsa_id in pairs:
        pair_q |= Q(tipo=tipo, risorsa_id=risorsa_id)

    qs = (
        RisorsaAttivita.objects.select_related("attivita")
        .filter(pair_q)
        .filter(
            attivita__data_inizio__lte=data_fine,
            attivita__data_fine__gte=data_inizio,
        )
    )
    if exclude_activity_id is not None:
        qs = qs.exclude(attivita_id=exclude_activity_id)

    return [
        {
            "tipo": row.tipo,
            "risorsa_id": row.risorsa_id,
            "attivita_id": row.attivita_id,
            "attivita_label": str(row.attivita),
            "data_inizio": row.attivita.data_inizio.isoformat()
            if row.attivita.data_inizio
            else None,
            "data_fine": row.attivita.data_fine.isoformat()
            if row.attivita.data_fine
            else None,
        }
        for row in qs
    ]


def validate_no_conflicts(
    assignments: list[ResourceAssignment] | list[tuple[str, str]],
    data_inizio: date,
    data_fine: date,
    exclude_activity_id: int | None = None,
) -> None:
    """Raise :class:`ResourceConflictError` if any assignment is already booked.

    Args:
        assignments: Candidate resources to book.
        data_inizio: Candidate activity start date.
        data_fine: Candidate activity end date.
        exclude_activity_id: Optional Attivita primary key to ignore.

    Returns:
        None: Returns silently when no conflicts are detected.

    Raises:
        ResourceConflictError: When at least one conflicting booking exists.
    """
    conflicts = find_conflicts(assignments, data_inizio, data_fine, exclude_activity_id)
    if conflicts:
        raise ResourceConflictError(conflicts)


def occupied_resource_ids(
    tipo: str,
    data_inizio: date,
    data_fine: date,
    exclude_activity_id: int | None = None,
) -> set[str]:
    """Return all resource IDs of a given type booked during a range.

    Convenience helper used by the wizard step 5 to populate the
    availability lists.  Shares the same overlap semantics as
    :func:`find_conflicts`.

    Args:
        tipo: Resource category.
        data_inizio: Candidate activity start date.
        data_fine: Candidate activity end date.
        exclude_activity_id: Optional Attivita primary key to ignore.

    Returns:
        set[str]: Resource IDs currently booked in the range.
    """
    from ui.models import RisorsaAttivita

    qs = RisorsaAttivita.objects.filter(
        tipo=tipo,
        attivita__data_inizio__lte=data_fine,
        attivita__data_fine__gte=data_inizio,
    )
    if exclude_activity_id is not None:
        qs = qs.exclude(attivita_id=exclude_activity_id)
    return set(qs.values_list("risorsa_id", flat=True))
