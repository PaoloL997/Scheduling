"""Predecessor links between activities of the same commessa.

Precedences are a real relation (``Attivita.predecessori``), so renaming an
activity never breaks the links that point at it.  Clients still speak in
``activity_id`` strings: this module resolves them to rows, within the
commessa, and refuses anything else.
"""

from __future__ import annotations

from collections.abc import Iterable

from ui.models import Attivita
from ui.services.identifiers import normalize_activity_id


def resolve_predecessors(
    commessa: str,
    activity_ids: Iterable[str],
    *,
    exclude_pk: int | None = None,
) -> list[Attivita]:
    """Return the activities of *commessa* named by *activity_ids*.

    Matching ignores case and encoding differences in the id.  Duplicates
    collapse; order follows *activity_ids*.

    Args:
        commessa: The commessa both sides belong to.
        activity_ids: Canonical ``activity_id`` strings.
        exclude_pk: The activity being edited, which may not precede itself.

    Returns:
        list[Attivita]: Resolved rows, in input order.

    Raises:
        ValueError: When an id names no activity of the commessa (or the
            activity itself).
    """
    wanted = [str(v).strip() for v in activity_ids if str(v).strip()]
    if not wanted:
        return []
    qs = Attivita.objects.filter(commessa=commessa)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    by_key = {normalize_activity_id(a.activity_id): a for a in qs}

    resolved: list[Attivita] = []
    invalid: list[str] = []
    for raw in wanted:
        att = by_key.get(normalize_activity_id(raw))
        if att is None:
            invalid.append(raw)
        elif att not in resolved:
            resolved.append(att)
    if invalid:
        raise ValueError(f"Predecessor non valido: {', '.join(invalid)}")
    return resolved


def set_predecessors(attivita: Attivita, activity_ids: Iterable[str]) -> list[Attivita]:
    """Replace the predecessors of a saved activity with *activity_ids*.

    Raises:
        ValueError: As :func:`resolve_predecessors`.
    """
    objs = resolve_predecessors(attivita.commessa, activity_ids, exclude_pk=attivita.pk)
    attivita.predecessori.set(objs)
    return objs
