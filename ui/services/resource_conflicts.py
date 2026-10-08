"""Resource conflict detection for activity scheduling.

A resource (testa, mandrino, tastatore, maschera, generatore) cannot be
assigned to two activities whose occupancy windows overlap.  This module
provides the single source of truth for that rule, used by both the wizard
save and the date-update endpoints.

Occupancy follows execution, not the planned calendar: completed activities
block through ``actual_finish``, in-progress ones through
``max(data_fine, effective_finish, today)`` — a running activity holds its
resources at least through today, however late it is.  Planned
``data_inizio`` / ``data_fine`` are unchanged.

Overlap semantics are inclusive on both ends: an activity that ends on day
``X`` conflicts with one that starts on day ``X`` for the same resource.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date

from django.db.models import Q

from ui.services.resource_types import (
    TIPO_LABELS as _TIPO_LABELS,
    TIPO_WITH_ARTICLE as _TIPO_WITH_ARTICLE,
)


def _format_date_it(iso: str | None) -> str:
    if not iso:
        return "?"
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def _format_date_range(data_inizio: str | None, data_fine: str | None) -> str:
    if data_inizio and data_fine:
        return f"{_format_date_it(data_inizio)} – {_format_date_it(data_fine)}"
    if data_inizio:
        return f"da {_format_date_it(data_inizio)}"
    if data_fine:
        return f"fino al {_format_date_it(data_fine)}"
    return "date non definite"


def format_conflict_message(
    conflicts: list[dict],
    *,
    action_hint: str | None = None,
) -> str:
    """Build a human-readable Italian message describing resource conflicts.

    Args:
        conflicts: Conflict descriptors as returned by :func:`find_conflicts`.
        action_hint: Optional closing sentence guiding the user on how to
            resolve the conflict (e.g. which UI action to take).

    Returns:
        str: A descriptive error message; falls back to ``Conflitto risorse``
            when *conflicts* is empty.
    """
    if not conflicts:
        return "Conflitto risorse"

    default_hint = "Sostituisci o rimuovi le risorse in conflitto prima di procedere."
    hint = action_hint or default_hint

    seen: set[tuple] = set()
    unique: list[dict] = []
    for conflict in conflicts:
        key = (
            conflict.get("tipo"),
            conflict.get("risorsa_id"),
            conflict.get("attivita_id"),
        )
        if key not in seen:
            seen.add(key)
            unique.append(conflict)

    if len(unique) == 1:
        conflict = unique[0]
        tipo_key = conflict.get("tipo", "")
        resource = _TIPO_WITH_ARTICLE.get(tipo_key, "la risorsa")
        rid = conflict.get("risorsa_id", "?")
        activity = conflict.get("attivita_label", "?")
        period = _format_date_range(
            conflict.get("data_inizio"), conflict.get("data_fine")
        )
        return (
            f"Conflitto risorse: {resource} «{rid}» è già assegnata "
            f"all'attività {activity} ({period}), che si sovrappone al periodo "
            f"selezionato. {hint}"
        )

    lines = [
        (
            f"Conflitto risorse: {len(unique)} risorse sono già occupate "
            f"nel periodo selezionato:"
        )
    ]
    for conflict in unique:
        tipo_key = conflict.get("tipo", "")
        tipo_label = _TIPO_LABELS.get(tipo_key, "Risorsa")
        rid = conflict.get("risorsa_id", "?")
        activity = conflict.get("attivita_label", "?")
        period = _format_date_range(
            conflict.get("data_inizio"), conflict.get("data_fine")
        )
        lines.append(f"• {tipo_label} «{rid}» — attività {activity} ({period})")
    lines.append(hint)
    return "\n".join(lines)


def occupation_window(attivita, today: date | None = None) -> tuple[date, date] | None:
    """Return the resource occupancy interval for an activity.

    Planned dates stay on the activity record.  Occupancy is:

    * planned only: ``[data_inizio, data_fine]``
    * in progress: ``[actual_start, max(data_fine, effective_finish, today)]``
    * completed: ``[actual_start or data_inizio, actual_finish]``

    An activity that is still open holds its resources through today even
    when its estimated finish is already behind: nothing is released until
    ``actual_finish`` is recorded.

    Args:
        attivita: An :class:`~ui.models.Attivita` (or duck type with the
            same date fields).
        today: Reference day; defaults to the local date.

    Returns:
        tuple[date, date] | None: Inclusive occupancy start and end, or
            ``None`` when the activity has no usable window.
    """
    from django.utils.timezone import localdate
    from ui.services.problem_detection import effective_finish

    if attivita.actual_finish:
        start = attivita.actual_start or attivita.data_inizio
        end = attivita.actual_finish
        if start is None:
            return None
        if end < start:
            end = start
        return start, end

    if attivita.actual_start:
        start = attivita.actual_start
        candidates = [
            start,
            attivita.data_fine,
            effective_finish(attivita),
            today or localdate(),
        ]
        return start, max(d for d in candidates if d is not None)

    if not (attivita.data_inizio and attivita.data_fine):
        return None
    return attivita.data_inizio, attivita.data_fine


@dataclass(frozen=True)
class _EditedActivity:
    """Duck type for :func:`occupation_window`: form dates, stored facts."""

    data_inizio: date | None
    data_fine: date | None
    actual_start: date | None
    actual_finish: date | None


def candidate_window(
    data_inizio: date,
    data_fine: date,
    attivita=None,
    today: date | None = None,
) -> tuple[date, date]:
    """Return the window the activity being edited would really hold.

    An availability picker asks "which resources are free while this
    activity holds them?", so it must test the same interval the save will
    validate.  For an activity that has already started, occupancy follows
    execution rather than the calendar: it begins on ``actual_start``, so a
    resource released on the *planned* start day is genuinely free.

    Planned dates come from the caller (the user may be editing them in the
    form), the actual dates from the stored record, and
    :func:`occupation_window` turns that pair into the real window.

    Args:
        data_inizio: Candidate start date as currently shown in the form.
        data_fine: Candidate end date as currently shown in the form.
        attivita: The activity being edited, or ``None`` for a new one.
        today: Reference day for :func:`occupation_window`.

    Returns:
        tuple[date, date]: The interval to test availability against —
            ``(data_inizio, data_fine)`` unchanged when the activity has not
            started yet.
    """
    if attivita is None or not (attivita.actual_start or attivita.actual_finish):
        return data_inizio, data_fine

    window = occupation_window(
        _EditedActivity(
            data_inizio=data_inizio,
            data_fine=data_fine,
            actual_start=attivita.actual_start,
            actual_finish=attivita.actual_finish,
        ),
        today=today,
    )
    return window or (data_inizio, data_fine)


def _candidate_overlap_q(data_inizio: date, data_fine: date) -> Q:
    """Return a wide ORM prefilter; :func:`occupation_window` is authoritative.

    Planned-dates-only filtering would miss in-progress delays whose
    occupancy extends past ``data_fine``.
    """
    start_touches = Q(attivita__data_inizio__lte=data_fine) | Q(
        attivita__actual_start__lte=data_fine
    )
    end_touches = (
        Q(attivita__data_fine__gte=data_inizio)
        | Q(attivita__actual_finish__gte=data_inizio)
        | Q(
            attivita__actual_start__isnull=False,
            attivita__actual_finish__isnull=True,
        )
    )
    return start_touches & end_touches


def _overlapping_bookings(
    pair_q: Q,
    data_inizio: date,
    data_fine: date,
    exclude_activity_id: int | None = None,
    today: date | None = None,
):
    """Yield bookings whose occupancy window overlaps the candidate range."""
    from ui.models import RisorsaAttivita

    qs = (
        RisorsaAttivita.objects.select_related("attivita")
        .filter(pair_q)
        .filter(_candidate_overlap_q(data_inizio, data_fine))
    )
    if exclude_activity_id is not None:
        qs = qs.exclude(attivita_id=exclude_activity_id)

    for row in qs:
        window = occupation_window(row.attivita, today=today)
        if window is None:
            continue
        occ_start, occ_end = window
        if occ_start <= data_fine and occ_end >= data_inizio:
            yield row, occ_start, occ_end


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

    def __init__(
        self,
        conflicts: list[dict],
        *,
        action_hint: str | None = None,
    ) -> None:
        """Initialise the exception with a list of conflict descriptors.

        Args:
            conflicts: Conflict descriptors as returned by
                :func:`find_conflicts`.
            action_hint: Optional closing sentence for the error message.
        """
        super().__init__(format_conflict_message(conflicts, action_hint=action_hint))
        self.conflicts = conflicts


def find_conflicts(
    assignments: list[ResourceAssignment] | list[tuple[str, str]],
    data_inizio: date,
    data_fine: date,
    exclude_activity_id: int | None = None,
    today: date | None = None,
) -> list[dict]:
    """Return existing bookings that overlap the candidate range.

    Iterates the provided assignments and queries :class:`RisorsaAttivita`
    for any row that matches both ``tipo`` and ``risorsa_id`` and whose
    parent activity occupancy window overlaps ``[data_inizio, data_fine]``
    inclusively on both ends.  Occupancy uses :func:`occupation_window`.

    Args:
        assignments: Candidate resources to book.  May be a list of
            :class:`ResourceAssignment` or ``(tipo, risorsa_id)`` tuples.
        data_inizio: Candidate activity start date.
        data_fine: Candidate activity end date.
        exclude_activity_id: Optional Attivita primary key to ignore (used
            when re-validating an existing activity during an edit flow).
        today: Reference day for the occupancy of running activities.

    Returns:
        list[dict]: One dictionary per conflicting booking with keys
            ``tipo``, ``risorsa_id``, ``attivita_id``, ``attivita_label``,
            ``data_inizio``, ``data_fine`` (occupancy dates).  Empty list
            when no conflict.
    """
    if not assignments:
        return []

    pairs = [
        (a.tipo, a.risorsa_id) if isinstance(a, ResourceAssignment) else tuple(a)
        for a in assignments
    ]

    pair_q = Q()
    for tipo, risorsa_id in pairs:
        pair_q |= Q(tipo=tipo, risorsa_id=risorsa_id)

    return [
        {
            "tipo": row.tipo,
            "risorsa_id": row.risorsa_id,
            "attivita_id": row.attivita_id,
            "attivita_label": str(row.attivita),
            "data_inizio": occ_start.isoformat(),
            "data_fine": occ_end.isoformat(),
        }
        for row, occ_start, occ_end in _overlapping_bookings(
            pair_q, data_inizio, data_fine, exclude_activity_id, today
        )
    ]


def validate_no_conflicts(
    assignments: list[ResourceAssignment] | list[tuple[str, str]],
    data_inizio: date,
    data_fine: date,
    exclude_activity_id: int | None = None,
    today: date | None = None,
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
    conflicts = find_conflicts(
        assignments, data_inizio, data_fine, exclude_activity_id, today
    )
    if conflicts:
        raise ResourceConflictError(conflicts)


def occupied_resource_ids(
    tipo: str,
    data_inizio: date,
    data_fine: date,
    exclude_activity_id: int | None = None,
    today: date | None = None,
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
    return {
        row.risorsa_id
        for row, _occ_start, _occ_end in _overlapping_bookings(
            Q(tipo=tipo), data_inizio, data_fine, exclude_activity_id, today
        )
    }


def bookings_not_ended(
    tipo: str,
    risorsa_ids: Iterable[str],
    since: date,
) -> Iterator[tuple]:
    """Yield current and future bookings of the given resources.

    A booking is returned when its occupancy window (see
    :func:`occupation_window`) has not ended before *since* — that is, the
    resource is either held on that day or will be held later on.  Used by the
    inventory availability column.

    Args:
        tipo: Resource category.
        risorsa_ids: Resource identifiers to look up.
        since: Reference day, usually today.

    Yields:
        tuple: ``(RisorsaAttivita, occupancy start, occupancy end)`` for every
            booking whose occupancy ends on or after *since*.
    """
    ids = [rid for rid in risorsa_ids if rid]
    if not ids:
        return
    yield from _overlapping_bookings(
        Q(tipo=tipo, risorsa_id__in=ids), since, date.max, today=since
    )
