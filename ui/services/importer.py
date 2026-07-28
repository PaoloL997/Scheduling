"""Background import service for loading data from source files into the Django ORM.

Provides a thread-safe import pipeline that reads from both an MS Project
``.mpp`` file and an Excel ``.xlsx`` file, then writes into the Django ORM
models :class:`~ui.models.Task`, :class:`~ui.models.Job`, and the five
inventory item models.  A module-level :class:`ImportStatus` enum tracks the
current state so that the web layer can expose it via a JSON endpoint.

Typical usage (called once from ``AppConfig.ready()`` then on each page load)::

    # At startup — store paths
    from ui.services.importer import configure
    configure(mpp_path, xlsx_path)

    # On each browser visit — reset ORM data and reimport
    from ui.services.importer import trigger_reimport
    trigger_reimport()
"""

import importlib
import logging
import threading
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_data_import = importlib.import_module("src.data.import")
import_from_mpp = _data_import.import_from_mpp
import_from_xlsx = _data_import.import_from_xlsx


class ImportStatus(Enum):
    """Represents the lifecycle state of the database import."""

    NOT_STARTED = "not_started"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"
    WRITING = "writing"


_status: ImportStatus = ImportStatus.NOT_STARTED
_error_detail: str = ""
_is_importing: bool = False
_lock = threading.Lock()
_hydrated_from_db: bool = False

_mpp_path: str = ""
_xlsx_path: str = ""
_mpp_mtime_at_last_import: Optional[float] = None


def configure(mpp_path: str, xlsx_path: str) -> None:
    """Store the file paths required by the import pipeline.

    Must be called once during application startup (from ``AppConfig.ready()``)
    before any call to :func:`trigger_reimport`.

    Args:
        mpp_path: Path to the MS Project ``.mpp`` source file.
        xlsx_path: Path to the Excel ``.xlsx`` source file.
    """
    global _mpp_path, _xlsx_path
    _mpp_path = mpp_path
    _xlsx_path = xlsx_path


def hydrate_status_from_db() -> None:
    """Restore in-memory import status from the persisted sync phase.

    Called lazily on first status read / hold-thread start so a process restart
    does not treat a populated working copy as ``NOT_STARTED``.
    """
    global _status, _error_detail, _hydrated_from_db
    with _lock:
        if _hydrated_from_db:
            return
    try:
        from ui.models import SyncSessionState
        from ui.services.sync_state import (
            dataset_has_working_copy,
            get_last_error,
            get_phase,
            mark_dirty,
            mark_ready,
        )

        phase = get_phase()
        error = get_last_error()
        has_data = dataset_has_working_copy()

        if phase == SyncSessionState.PHASE_WRITING:
            # Crash mid write-back: keep data, mark dirty for retry.
            msg = error or "Write-back precedente interrotto."
            mark_dirty(msg)
            new_status = ImportStatus.READY
            new_error = msg
        elif phase == SyncSessionState.PHASE_DIRTY:
            new_status = ImportStatus.READY
            new_error = error
        elif phase == SyncSessionState.PHASE_READY:
            new_status = ImportStatus.READY
            new_error = ""
        elif phase == SyncSessionState.PHASE_LOADING and has_data:
            # Import interrupted but data survived — resume as ready.
            mark_ready()
            new_status = ImportStatus.READY
            new_error = ""
        elif has_data:
            # Anomalous: data without a matching phase — preserve it.
            mark_ready(error or "Working copy recuperata dopo restart.")
            new_status = ImportStatus.READY
            new_error = error
        else:
            new_status = ImportStatus.NOT_STARTED
            new_error = ""

        with _lock:
            _status = new_status
            _error_detail = new_error
            _hydrated_from_db = True
        logger.info(
            "Import status idratato da DB: phase=%s → %s (has_data=%s)",
            phase,
            new_status.value,
            has_data,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile idratare lo stato sync dal DB: %s", exc)
        with _lock:
            _hydrated_from_db = True


def get_status() -> ImportStatus:
    """Return the current import status in a thread-safe manner.

    Returns:
        ImportStatus: The current status value.
    """
    hydrate_status_from_db()
    with _lock:
        return _status


def get_error_detail() -> str:
    """Return a human-readable description of the last import error.

    Returns:
        str: The error message, or an empty string if no error occurred.
    """
    hydrate_status_from_db()
    with _lock:
        return _error_detail


def is_mpp_stale() -> bool:
    """Return True if the .mpp file has been modified since the last import.

    Returns True also when no import has completed yet (``_mpp_mtime_at_last_import``
    is None), which forces a fresh import on the first user login.

    Returns:
        bool: Whether the source .mpp is newer than the last import snapshot.
    """
    import os

    if not _mpp_path:
        return False
    if _mpp_mtime_at_last_import is None:
        return True
    try:
        return os.path.getmtime(_mpp_path) > _mpp_mtime_at_last_import
    except OSError:
        return False


def _set_status(status: ImportStatus, error: str = "") -> None:
    """Update the module-level status in a thread-safe manner.

    Also mirrors the status onto :class:`~ui.models.SyncSessionState` so it
    survives process restarts.

    Args:
        status: The new status to set.
        error: Optional human-readable error description.
    """
    global _status, _error_detail, _hydrated_from_db
    with _lock:
        _status = status
        _error_detail = error
        _hydrated_from_db = True

    try:
        from ui.models import SyncSessionState
        from ui.services import sync_state

        if status == ImportStatus.NOT_STARTED:
            sync_state.mark_idle()
        elif status == ImportStatus.LOADING:
            sync_state.mark_loading()
        elif status == ImportStatus.READY:
            if error:
                sync_state.mark_dirty(error)
            else:
                sync_state.mark_ready()
        elif status == ImportStatus.WRITING:
            sync_state.mark_writing()
        elif status == ImportStatus.ERROR:
            sync_state.set_phase(SyncSessionState.PHASE_DIRTY, error)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile persistente SyncSessionState: %s", exc)


_on_complete_callback: Optional[Callable] = None


def begin_writeback() -> None:
    """Set the import status to WRITING to signal an active write-back operation.

    Called by :mod:`ui.services.project_session` immediately before writing
    back to the source files, so the frontend polling loop blocks all
    interactions and displays a "write in progress" overlay.
    """
    _set_status(ImportStatus.WRITING)


def end_writeback(success: bool, error: str = "") -> None:
    """Finalize write-back status without forcing a dataset reset.

    Args:
        success: Whether the write-back completed successfully.
        error: Optional diagnostic detail when ``success`` is ``False``.
    """
    if success:
        _set_status(ImportStatus.READY)
        return
    _set_status(ImportStatus.READY, error)


def set_files_readonly() -> None:
    """Mark the configured source files as read-only at the OS level.

    Sets the read-only attribute on the ``.mpp`` and ``.xlsx`` source files
    using ``os.chmod``.  On Windows this removes the write bit, which prevents
    accidental external modifications while the web app holds the data in its
    ORM.  Failures are logged as warnings rather than exceptions so a missing
    or already-locked file does not abort the import.
    """
    import os
    import stat

    for path in (_mpp_path, _xlsx_path):
        if not path:
            continue
        if not os.path.exists(path):
            logger.warning("set_files_readonly: file non trovato: %s", path)
            continue
        try:
            os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
            logger.info("File impostato in sola lettura: %s", os.path.basename(path))
        except OSError as exc:
            logger.warning(
                "Impossibile impostare sola lettura su %s: %s",
                os.path.basename(path),
                exc,
            )


def set_files_writable() -> None:
    """Restore write permissions on the configured source files.

    Removes the read-only attribute set by :func:`set_files_readonly` so that
    the write-back service (COM session and ``openpyxl`` export) can overwrite
    the files.  Failures are logged as warnings.
    """
    import os
    import stat

    for path in (_mpp_path, _xlsx_path):
        if not path:
            continue
        if not os.path.exists(path):
            logger.warning("set_files_writable: file non trovato: %s", path)
            continue
        try:
            os.chmod(
                path,
                stat.S_IREAD
                | stat.S_IWRITE
                | stat.S_IRGRP
                | stat.S_IWGRP
                | stat.S_IROTH
                | stat.S_IWOTH,
            )
            logger.info(
                "File riportato in lettura/scrittura: %s", os.path.basename(path)
            )
        except OSError as exc:
            logger.warning(
                "Impossibile ripristinare scrittura su %s: %s",
                os.path.basename(path),
                exc,
            )


def trigger_reimport(on_complete: Optional[Callable] = None) -> None:
    """Start a fresh import pipeline from the configured source files.

    If an import is already in progress the call is a no-op, so rapid
    successive page loads never spawn more than one import thread at a time.
    The import status is reset to :attr:`ImportStatus.LOADING` before the
    thread is started so the frontend polling loop immediately reflects the
    new state.

    Args:
        on_complete: Optional callable invoked (from the import thread) after
            the import completes successfully.  Useful for chaining actions
            that must wait for the import to finish (e.g. acquiring the COM
            lock).

    Raises:
        RuntimeError: If :func:`configure` has not been called first.
    """
    global _is_importing, _on_complete_callback
    if not _mpp_path:
        raise RuntimeError("configure() must be called before trigger_reimport()")

    with _lock:
        if _is_importing:
            logger.debug("Importazione già in corso, richiesta ignorata.")
            return
        _is_importing = True
        _on_complete_callback = on_complete

    _set_status(ImportStatus.LOADING)

    thread = threading.Thread(
        target=_run_import,
        daemon=True,
        name="db-import-thread",
    )
    thread.start()


def cleanup_for_next_session() -> None:
    """Reset importer state so the next session starts from a clean slate.

    Sets the status back to :attr:`ImportStatus.NOT_STARTED` and clears the
    mtime snapshot.  The ORM tables (Attivita, Job, inventories) are cleared by
    :func:`~ui.services.reset.reset_app_dataset`, which is called by
    ``_hold_thread`` immediately before this function.

    This function is thread-safe: the status update uses the internal lock.
    """
    global _mpp_mtime_at_last_import
    _set_status(ImportStatus.NOT_STARTED)
    with _lock:
        _mpp_mtime_at_last_import = None
    logger.info("Stato importer resettato per la prossima sessione.")


def _run_import() -> None:
    """Execute the full import pipeline and update the module status.

    Restores write permissions on the source files before opening them so that
    a read-only attribute left over from a previous session (set by
    :func:`set_files_readonly`) never causes MS Project to open the file in
    read-only mode and show a warning dialog.

    Initialises the COM library for this thread before invoking the MS Project
    importer (required by ``win32com`` on any thread other than the main one),
    then unconditionally uninitialises it on exit.  Any exception sets the
    status to :attr:`ImportStatus.ERROR` and stores the exception message.
    The ``_is_importing`` flag is always cleared in the ``finally`` block so
    that a subsequent call to :func:`trigger_reimport` is never blocked by a
    failed previous run.

    Imports from the ``.mpp`` (tasks + inventory) and from the jobs ``.xlsx``.
    Inventory durability is achieved by writing all resources to the ``.mpp``
    at session end (via the COM write-back service), so they are automatically
    restored here on the next session start.
    """
    import os

    global _is_importing, _mpp_mtime_at_last_import
    import pythoncom

    pythoncom.CoInitialize()
    try:
        set_files_writable()

        try:
            mtime_before = os.path.getmtime(_mpp_path)
        except OSError:
            mtime_before = None

        import_from_mpp(_mpp_path)
        import_from_xlsx(_xlsx_path)

        # Detect problems for all imported activities and restore previously
        # saved resolution notes (they survive the dataset reset via the
        # RisoluzioneProblema table).
        try:
            from ui.services.problem_detection import (
                detect_for_all_activities,
                restore_resolved_notes,
            )

            detect_for_all_activities()
            restore_resolved_notes()
        except Exception as exc:  # noqa: BLE001
            logger.error("Errore nel rilevamento problemi post-import: %s", exc)

        if mtime_before is not None:
            _mpp_mtime_at_last_import = mtime_before
        _set_status(ImportStatus.READY)
        logger.info("Importazione completata con successo da: %s", _mpp_path)
        set_files_readonly()
        callback = _on_complete_callback
        if callback is not None:
            try:
                callback()
            except Exception as exc:  # noqa: BLE001
                logger.error("Errore nel callback on_complete: %s", exc)
    except Exception as exc:  # noqa: BLE001
        _set_status(ImportStatus.ERROR, str(exc))
        logger.error("Importazione fallita: %s", exc)
    finally:
        pythoncom.CoUninitialize()
        with _lock:
            _is_importing = False
