"""Bring the problem notes in line with the data — after a write, never on a read.

Every operational note (``acquisto``, ``spostamento``, ``sovrapposizione``,
``ritardo``) is derived from the activities, their assignments and the
calendar.  Pages and APIs that *read* the plan never touch these rows: they
display what the last write left behind.  The write paths call
:func:`reconcile` once they are done, so two concurrent page loads can never
race each other and opening a page never changes the plan.

The one condition that changes without any write is ``ritardo`` (never
started and past the planned finish), which depends on today's date.
:func:`ensure_daily_sync` closes that gap once a day per process; the
``reconcile_problems`` management command does the same job from a scheduler.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date

logger = logging.getLogger(__name__)

_last_daily_sync: date | None = None


def reconcile(attivita_ids: Iterable[int] | None = None) -> dict:
    """Reconcile the notes after a write.

    Order matters: delay overlaps are swapped first (a swap changes the
    resources of the blocked activity, and its own ``acquisto`` /
    ``spostamento`` notes are refreshed by that write), then the activities
    the caller touched get their ``acquisto`` / ``spostamento`` notes
    refreshed against the final assignments.  ``ritardo`` notes are synced
    for everyone inside the overlap pass.

    Args:
        attivita_ids: Primary keys of the activities the write touched.
            ``None`` refreshes every activity (management command, tests).

    Returns:
        dict: The overlap-resolution payload (``swapped_blocked_ids`` and
            ``activity_updates`` keyed by pk) the planning page applies.
    """
    from ui.models import Attivita
    from ui.services.overlap_reassign import auto_resolve_resource_overlaps
    from ui.services.problem_detection import refresh_problems_for_activities

    result = auto_resolve_resource_overlaps()

    qs = Attivita.objects.prefetch_related("risorse")
    if attivita_ids is not None:
        ids = [int(pk) for pk in attivita_ids]
        if not ids:
            return result
        qs = qs.filter(pk__in=ids)
    created = refresh_problems_for_activities(qs)
    if created:
        logger.info("reconcile: %d nuovi problemi rilevati.", created)
    return result


def reconcile_after_delete() -> dict:
    """Close the notes that pointed at activities which no longer exist.

    Deleting an activity cascades its own notes, but an open
    ``sovrapposizione`` on its peer still references it: one overlap pass
    closes those and re-syncs ``ritardo``.
    """
    from ui.services.overlap_reassign import auto_resolve_resource_overlaps

    return auto_resolve_resource_overlaps()


def ensure_daily_sync(today: date | None = None) -> bool:
    """Sync the calendar-driven ``ritardo`` notes once per day per process.

    Cheap (two queries) and idempotent, so it is the only write a read path
    may trigger.  Returns ``True`` when the sync ran.
    """
    global _last_daily_sync

    from ui.services.problem_detection import sync_overdue_problems

    reference = today or date.today()
    if _last_daily_sync == reference:
        return False
    sync_overdue_problems(today=reference)
    _last_daily_sync = reference
    return True


def reset_daily_sync() -> None:
    """Forget the last daily sync (tests)."""
    global _last_daily_sync
    _last_daily_sync = None
