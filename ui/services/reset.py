"""App dataset reset utility.

Provides :func:`reset_app_dataset`, which clears all Django ORM records that
mirror or stage content from the ``.mpp`` file.  This function is called
before every fresh import — both when the login signal detects staleness and
when the COM lock thread self-starts an import after a server restart with
surviving sessions.

Keeping this logic in a dedicated module avoids circular imports between
:mod:`ui.signals` and :mod:`ui.services.project_session`, both of which need
to call it.
"""

import logging

logger = logging.getLogger(__name__)


def reset_app_dataset(*, force: bool = False) -> None:
    """Clear all app-side data that mirrors or stages content from the .mpp file.

    Deletes all :class:`~ui.models.Attivita` records (cascading to their
    :class:`~ui.models.RisorsaAttivita` children), all
    :class:`~ui.models.MppSyncQueue` entries, and all ORM records that were
    populated by the import pipeline (:class:`~ui.models.Attivita`,
    :class:`~ui.models.Job`, and the five inventory item models).

    After the reset, the fresh import from the ``.mpp`` file becomes the
    single source of truth.

    Args:
        force: When ``False`` (default), refuse to wipe if a working copy or
            pending queue still exists.  Pass ``True`` only after a confirmed
            durable write-back or an explicit forced reimport.
    """
    from ui.models import (
        Attivita,
        GeneratoreItem,
        Job,
        MandrinoItem,
        MascheraItem,
        MppSyncQueue,
        TastatoreItem,
        TestaItem,
    )
    from ui.services.sync_state import dataset_has_working_copy, pending_queue_count

    if not force and dataset_has_working_copy():
        logger.error(
            "reset_app_dataset bloccato: working copy presente "
            "(coda=%d). Usare force=True solo dopo write-back riuscito.",
            pending_queue_count(),
        )
        raise RuntimeError(
            "Reset dataset rifiutato: modifiche locali o coda ancora presenti."
        )

    deleted_att, _ = Attivita.objects.all().delete()
    deleted_queue, _ = MppSyncQueue.objects.all().delete()
    deleted_jobs, _ = Job.objects.all().delete()
    deleted_inv = (
        TestaItem.objects.all().delete()[0]
        + MandrinoItem.objects.all().delete()[0]
        + TastatoreItem.objects.all().delete()[0]
        + MascheraItem.objects.all().delete()[0]
        + GeneratoreItem.objects.all().delete()[0]
    )
    logger.info(
        "Reset dataset: %d Attivita, %d MppSyncQueue, %d Job, %d inventari eliminati.",
        deleted_att,
        deleted_queue,
        deleted_jobs,
        deleted_inv,
    )
