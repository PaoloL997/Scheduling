"""Persistent MS Project COM session manager.

Manages the source file lifecycle and write-back to `.mpp` and `.xlsx`.
After the import pipeline completes the source files are marked read-only
so that no external process can accidentally modify them while the web app
holds live data in its ORM.

Presence detection
------------------
The server cannot be notified when a browser tab closes.  The frontend sends
a heartbeat ping (``GET /api/heartbeat/``) every :data:`_HEARTBEAT_INTERVAL`
seconds **only while the user is active** (mouse, keyboard, touch).  A
dedicated watchdog thread wakes every :data:`_HEARTBEAT_INTERVAL` seconds and
removes sessions whose last heartbeat is older than
:data:`_HEARTBEAT_TIMEOUT`.  Explicit logouts take effect immediately via
:meth:`ProjectSession.unregister_session`.

Lifecycle
---------
1. **First user opens a page** — :meth:`ProjectSession.register_session` is
   called (either by the ``user_logged_in`` signal or the tracking middleware).
   The session set grows from empty to non-empty → ``_hold_thread`` starts.
2. **``_hold_thread``** — waits for the import pipeline to complete.  The
   pipeline marks both source files read-only when it sets ``READY`` status.
3. **Heartbeat loop** — runs with no COM session held; source files stay
   read-only throughout this phase.
4. **All sessions gone** — either explicit logout via
   :meth:`ProjectSession.unregister_session` or heartbeat timeout detected by
   the watchdog → ``_release_event`` is set.
5. **Write-back** — status is set to ``WRITING``; files are made writable;
   a COM session is opened briefly to flush :class:`~ui.models.MppSyncQueue`
   entries and save; finally the jobs ``.xlsx`` is overwritten via
   ``openpyxl``.
6. **Cleanup** — ORM tables are deleted; status is reset to ``NOT_STARTED``
   so the *next* first login always performs a fresh import.
"""

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)


def _heartbeat_interval_seconds() -> int:
    from ui.services.runtime_config import load_runtime_config

    return load_runtime_config().heartbeat_interval_seconds


def _heartbeat_timeout_seconds() -> int:
    from ui.services.runtime_config import load_runtime_config

    return load_runtime_config().heartbeat_timeout_seconds


class ProjectSession:
    """Manages a persistent COM lock on the MS Project file.

    This class is intended to be used as a module-level singleton
    (``project_session``).  All public methods are thread-safe.
    """

    def __init__(self) -> None:
        self._active_sessions: set[str] = set()
        self._session_last_seen: dict[str, float] = {}
        self._release_event = threading.Event()
        self._lock = threading.Lock()
        self._hold_thread: threading.Thread | None = None
        self._hold_active: bool = False
        self._watchdog_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_session(self, session_key: str) -> None:
        """Track a session without refreshing its heartbeat timestamp.

        Used after a server restart (middleware) or on login so the hold thread
        starts even when the in-memory session set was lost.  Does **not**
        count as user activity — only :meth:`heartbeat` / :meth:`register_session`
        refresh the idle clock.

        Args:
            session_key: Django session key for the authenticated user.
        """
        if not session_key:
            return
        with self._lock:
            was_empty = len(self._active_sessions) == 0
            was_new = session_key not in self._active_sessions
            self._active_sessions.add(session_key)
            if was_new:
                self._session_last_seen[session_key] = time.time()
            if was_empty and not self._hold_active:
                self._release_event.clear()
                self._start_hold_thread()
            self._ensure_watchdog_locked()

    def register_session(self, session_key: str) -> None:
        """Track a session and refresh its heartbeat timestamp.

        Called by explicit keep-alive pings (``/api/heartbeat/``) and on login.
        Unlike :meth:`ensure_session`, every call resets the idle timer.

        Args:
            session_key: Django session key for the authenticated user.
        """
        self.ensure_session(session_key)
        self.heartbeat(session_key)

    def heartbeat(self, session_key: str) -> None:
        """Refresh the last-seen timestamp for an active session.

        Called by the ``/api/heartbeat/`` endpoint on every frontend ping.
        Has no effect if the session is not currently tracked (e.g. after an
        explicit logout that already removed it).

        Args:
            session_key: Django session key to refresh.
        """
        if not session_key:
            return
        with self._lock:
            if session_key in self._active_sessions:
                self._session_last_seen[session_key] = time.time()

    def unregister_session(self, session_key: str) -> None:
        """Record that a session has ended; trigger write-back if it was the last.

        Called immediately by the ``user_logged_out`` signal so explicit
        logouts take effect without waiting for the heartbeat watchdog.

        Args:
            session_key: Django session key for the user who logged out.
        """
        if not session_key:
            return
        with self._lock:
            self._active_sessions.discard(session_key)
            self._session_last_seen.pop(session_key, None)
            if len(self._active_sessions) == 0:
                logger.info("Ultimo utente disconnesso — avvio write-back .mpp.")
                self._release_event.set()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _expire_stale_sessions(self) -> None:
        """Remove sessions that have not sent a heartbeat within the timeout window.

        If expiring stale sessions empties :attr:`_active_sessions`, sets
        ``_release_event`` to trigger write-back.
        """
        now = time.time()
        with self._lock:
            expired = [
                key
                for key, ts in list(self._session_last_seen.items())
                if key in self._active_sessions
                and now - ts > _heartbeat_timeout_seconds()
            ]
            for key in expired:
                timeout_seconds = _heartbeat_timeout_seconds()
                logger.info(
                    "Sessione scaduta per inattività (%ds senza heartbeat): %s...",
                    timeout_seconds,
                    key[:8],
                )
                self._active_sessions.discard(key)
                self._session_last_seen.pop(key, None)
            if expired and not self._active_sessions:
                logger.info(
                    "Tutte le sessioni scadute per inattività — avvio write-back .mpp."
                )
                self._release_event.set()

    def _ensure_watchdog_locked(self) -> None:
        """Start the session watchdog thread if not already running.

        Must be called while ``_lock`` is held.
        """
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            return
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name="session-watchdog",
        )
        self._watchdog_thread.start()

    def _watchdog_loop(self) -> None:
        """Expire idle sessions and restart the hold thread if it died unexpectedly."""
        while True:
            time.sleep(_heartbeat_interval_seconds())
            with self._lock:
                has_sessions = bool(self._active_sessions)
            if not has_sessions:
                continue
            self._expire_stale_sessions()
            with self._lock:
                if self._active_sessions and not self._hold_active:
                    logger.warning(
                        "Watchdog: hold thread assente con sessioni attive — riavvio."
                    )
                    self._release_event.clear()
                    self._start_hold_thread()

    def _start_hold_thread(self) -> None:
        """Spawn the background thread that holds the COM lock.

        Must be called while ``_lock`` is held.
        """
        self._hold_active = True
        self._hold_thread = threading.Thread(
            target=self._hold,
            daemon=True,
            name="mpp-lock-thread",
        )
        self._hold_thread.start()
        logger.info("Thread di lock .mpp avviato.")

    def _hold(self) -> None:
        """Background thread: import → mark files read-only → heartbeat watchdog → write-back → cleanup.

        Lifecycle phases:

        1. **Import** — if the status is ``NOT_STARTED``, resets the ORM and
           triggers a fresh import.  Waits until the import finishes (READY or
           ERROR).
        2. **Heartbeat loop** — polls :meth:`_expire_stale_sessions` every
           :data:`_HEARTBEAT_INTERVAL` seconds until all sessions are gone and
           ``_release_event`` is set.  The source files are read-only during
           this phase (set by the import pipeline).
        3. **Write-back** — makes files writable, opens a COM session, applies
           pending :class:`~ui.models.MppSyncQueue` entries, saves and closes
           the project, then exports the updated jobs to ``.xlsx``.
        4. **Cleanup** — resets ORM tables and the importer state so the next
           first-login always triggers a fresh import.

        The ``released`` flag gates cleanup so that early exits (import error,
        file locked) do not wipe data that active sessions are still using.
        """
        import pythoncom

        from ui.services.importer import (
            ImportStatus,
            begin_writeback,
            get_status,
            set_files_writable,
            trigger_reimport,
        )
        from ui.services.writeback import write_pending_changes

        import importlib

        project_module = importlib.import_module("src.data.project")
        ProjectClass = project_module.Project
        _dispatch_msp = project_module._dispatch_msp

        pythoncom.CoInitialize()
        released = False
        try:
            # If the import has not started yet (first login or server restart
            # with surviving session cookie), reset stale ORM data and kick off
            # the import now.  Also handles WRITING status left from a failed
            # or interrupted write-back attempt.
            if get_status() in (ImportStatus.NOT_STARTED, ImportStatus.WRITING):
                logger.info(
                    "Status '%s' — reset dataset app e avvio import da _hold_thread.",
                    get_status().value,
                )
                from ui.services.reset import reset_app_dataset

                reset_app_dataset()
                trigger_reimport()

            # --- Wait for any running import to finish -----------------
            while get_status() == ImportStatus.LOADING:
                time.sleep(0.5)

            if get_status() != ImportStatus.READY:
                logger.error(
                    "Import fallito — impossibile avviare la sessione. "
                    "I file non saranno bloccati."
                )
                return

            # --- Heartbeat watchdog loop --------------------------------
            # Files are read-only (set by the import pipeline).
            # No COM session is held during this phase.
            while not self._release_event.is_set():
                self._release_event.wait(timeout=_heartbeat_interval_seconds())
                if not self._release_event.is_set():
                    self._expire_stale_sessions()

            released = True

            # --- Write-back phase ---------------------------------------
            begin_writeback()
            logger.info(
                "Avvio write-back: ripristino permessi di scrittura sui file sorgente."
            )
            set_files_writable()

            from ui.services.runtime_config import load_runtime_config

            config = load_runtime_config()
            mpp_path = config.mpp_path
            xlsx_path = config.xlsx_path

            app = None
            try:
                app = _dispatch_msp()
                app.Visible = False
                app.FileOpen(os.path.abspath(mpp_path))
                project_com = app.ActiveProject

                if project_com.ReadOnly:
                    logger.error(
                        "Il file .mpp è ancora in sola lettura dopo il ripristino dei permessi "
                        "(aperto da un processo esterno?). Write-back annullato — dati preservati."
                    )
                    released = False
                    return

                wrapper = ProjectClass.from_open_app(app, project_com)
                logger.info(
                    "COM session aperta per write-back: %s", os.path.basename(mpp_path)
                )

                save_ok = False
                try:
                    write_pending_changes(wrapper)
                    save_ok = True
                except Exception as exc:  # noqa: BLE001
                    logger.error("Errore durante il write-back: %s", exc)

                if save_ok:
                    app.FileSave()
                    logger.info("Write-back completato. File .mpp salvato.")
                else:
                    logger.warning("Write-back non riuscito — file .mpp NON salvato.")

            except Exception as exc:  # noqa: BLE001
                logger.error("Errore nella fase COM write-back: %s", exc)
            finally:
                if app is not None:
                    try:
                        app.FileCloseEx(Save=0)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        app.Quit()
                    except Exception:  # noqa: BLE001
                        pass
                    logger.info("COM session chiusa.")

            # Export the current Job table to .xlsx (full overwrite)
            try:
                from src.data.export_xlsx import export_to_xlsx

                export_to_xlsx(xlsx_path)
            except Exception as exc:  # noqa: BLE001
                logger.error("Errore durante l'export xlsx: %s", exc)

        except Exception as exc:  # noqa: BLE001
            logger.error("Errore nel thread di lock .mpp: %s", exc)
        finally:
            # Full cleanup only when all users have disconnected.  For early
            # exits (import error, file locked by another process) we leave
            # the data intact so active sessions can continue.
            if released:
                try:
                    from ui.services.reset import reset_app_dataset
                    from ui.services.importer import cleanup_for_next_session

                    reset_app_dataset()
                    cleanup_for_next_session()
                except Exception as exc:  # noqa: BLE001
                    logger.error("Errore durante il cleanup finale: %s", exc)

            with self._lock:
                self._hold_active = False
                # Only restart if the hold cycle completed normally (released).
                # Do not restart for early exits — the file or import is
                # unavailable, so retrying immediately would loop forever.
                if released and self._active_sessions:
                    logger.info(
                        "Nuove sessioni rilevate dopo write-back — riavvio ciclo."
                    )
                    self._release_event.clear()
                    self._start_hold_thread()

            pythoncom.CoUninitialize()


# Module-level singleton used by signals and middleware.
project_session = ProjectSession()
