"""Persistent sync-session phase for durable write-back recovery.

The in-memory :class:`~ui.services.importer.ImportStatus` is mirrored here so a
server restart does not treat a populated working copy as a brand-new session
and wipe pending changes before they reach ``.mpp`` / ``.xlsx``.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from django.db import transaction

logger = logging.getLogger(__name__)

_SINGLETON_PK = 1


def get_or_create_state():
    """Return the singleton :class:`~ui.models.SyncSessionState` row."""
    from ui.models import SyncSessionState

    state, _ = SyncSessionState.objects.get_or_create(
        pk=_SINGLETON_PK,
        defaults={"phase": SyncSessionState.PHASE_IDLE, "last_error": ""},
    )
    return state


def get_phase() -> str:
    """Return the persisted phase string."""
    return get_or_create_state().phase


def get_last_error() -> str:
    """Return the last persisted sync/write-back error message."""
    return get_or_create_state().last_error


def set_phase(phase: str, error: str = "") -> None:
    """Persist the sync phase and optional error detail.

    Args:
        phase: One of :class:`~ui.models.SyncSessionState` ``PHASE_*`` values.
        error: Human-readable detail; cleared when empty.
    """

    with transaction.atomic():
        state = get_or_create_state()
        state.phase = phase
        state.last_error = error or ""
        state.save(update_fields=["phase", "last_error", "updated_at"])
        logger.debug("SyncSessionState → %s (%s)", phase, error or "ok")


def dataset_has_working_copy() -> bool:
    """Return True when ORM tables still hold session data worth preserving."""
    from ui.models import Attivita, Job, MppSyncQueue

    return (
        MppSyncQueue.objects.exists()
        or Attivita.objects.exists()
        or Job.objects.exists()
    )


def pending_queue_count() -> int:
    """Return the number of pending :class:`~ui.models.MppSyncQueue` entries."""
    from ui.models import MppSyncQueue

    return MppSyncQueue.objects.count()


def should_fresh_import() -> bool:
    """Return True only when it is safe to wipe the ORM and reimport sources.

    A fresh import is allowed when there is no working copy.  Any pending queue
    entries or populated activity/job tables mean the previous session must be
    resumed or flushed first.
    """
    return not dataset_has_working_copy()


def mark_idle() -> None:
    """Mark the session as fully cleaned up (post successful write-back)."""
    from ui.models import SyncSessionState

    set_phase(SyncSessionState.PHASE_IDLE)


def mark_loading() -> None:
    from ui.models import SyncSessionState

    set_phase(SyncSessionState.PHASE_LOADING)


def mark_ready(error: str = "") -> None:
    from ui.models import SyncSessionState

    set_phase(SyncSessionState.PHASE_READY, error)


def mark_writing() -> None:
    from ui.models import SyncSessionState

    set_phase(SyncSessionState.PHASE_WRITING)


def mark_dirty(error: str) -> None:
    from ui.models import SyncSessionState

    set_phase(SyncSessionState.PHASE_DIRTY, error)


def backup_source_files(
    mpp_path: str, xlsx_path: str, backup_dir: str | Path | None = None
) -> list[Path]:
    """Copy source files to timestamped backups before write-back.

    Args:
        mpp_path: Path to ``dataset.mpp``.
        xlsx_path: Path to ``jobs.xlsx``.
        backup_dir: Optional target directory (defaults next to each file).

    Returns:
        List of created backup paths (missing sources are skipped).
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    created: list[Path] = []
    for src in (mpp_path, xlsx_path):
        if not src:
            continue
        path = Path(src)
        if not path.is_file():
            logger.warning("Backup saltato, file assente: %s", path)
            continue
        target_dir = Path(backup_dir) if backup_dir else path.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / f"{path.stem}.pre-writeback-{stamp}{path.suffix}"
        shutil.copy2(path, dest)
        created.append(dest)
        logger.info("Backup creato: %s", dest.name)
    return created


def clear_sync_queue() -> int:
    """Delete all pending queue entries after a confirmed durable write-back."""
    from ui.models import MppSyncQueue

    deleted, _ = MppSyncQueue.objects.all().delete()
    return deleted
