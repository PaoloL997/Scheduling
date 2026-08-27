import logging
import os
import time
from datetime import date, datetime
from typing import Optional

import win32com.client

from src.data.models import Resource, Task

logger = logging.getLogger(__name__)

# MS Project sentinel date used for "not set" optional date fields.
_MSP_SENTINEL_YEAR = 1990
_MSP_CLEAR_DATE = date(1989, 1, 1)
_MSP_START_HOUR = 9
_MSP_FINISH_HOUR = 18
_UNSET = object()

# COM HRESULT for "server process could not be started" (0x80080005).
_CO_E_SERVER_EXEC_FAILURE = -2146959355
_DISPATCH_RETRY_INTERVAL = 0.5  # default seconds between retry attempts
_DISPATCH_RETRY_MAX_WAIT = 5.0  # default maximum total wait before giving up


def _dispatch_retry_config() -> tuple[float, float]:
    """Return (interval, max_wait) with DB overrides when available."""
    try:
        from ui.services.runtime_config import load_runtime_config

        cfg = load_runtime_config()
        return (
            cfg.msp_dispatch_retry_interval_seconds,
            cfg.msp_dispatch_retry_max_wait_seconds,
        )
    except Exception:
        return (_DISPATCH_RETRY_INTERVAL, _DISPATCH_RETRY_MAX_WAIT)


def _dispatch_msp() -> object:
    """Create a new isolated MSProject.Application COM instance.

    Uses ``DispatchEx`` to always start a fresh process, avoiding reuse of a
    potentially stale COM server registered in the Running Object Table.

    On ``CO_E_SERVER_EXEC_FAILURE`` (0x80080005), polls for up to
    ``_DISPATCH_RETRY_MAX_WAIT`` seconds and retries — handles the race where a
    previous Project instance has not yet fully exited.

    Returns:
        A new ``MSProject.Application`` COM object.

    Raises:
        pywintypes.com_error: If MS Project cannot be started after retries.
        RuntimeError: If the server repeatedly fails to start within the timeout.
    """
    import pywintypes

    try:
        return win32com.client.DispatchEx("MSProject.Application")
    except pywintypes.com_error as exc:
        hresult = exc.args[0] if exc.args else None
        if hresult != _CO_E_SERVER_EXEC_FAILURE:
            raise
        logger.warning(
            "MSProject.Application non avviabile (0x%08X) — attesa e nuovo tentativo.",
            hresult & 0xFFFFFFFF,
        )

    # Poll until the retry window expires, retrying DispatchEx each interval.
    retry_interval, retry_max_wait = _dispatch_retry_config()
    elapsed = 0.0
    last_exc: Optional[Exception] = None
    while elapsed < retry_max_wait:
        time.sleep(retry_interval)
        elapsed += retry_interval
        try:
            return win32com.client.DispatchEx("MSProject.Application")
        except pywintypes.com_error as retry_exc:
            last_exc = retry_exc
            if retry_exc.args[0] != _CO_E_SERVER_EXEC_FAILURE:
                raise

    raise RuntimeError(
        "MSProject.Application non risponde dopo il retry "
        f"({retry_max_wait:.0f}s). "
        "Verificare che MS Project sia installato, non bloccato da finestre di dialogo "
        f"e che non ci siano processi WINPROJ.EXE bloccati. Errore COM: {last_exc}"
    )


def _safe_close_and_quit(app) -> None:
    """Close the active project and quit MS Project without raising.

    ``FileCloseEx`` is unreliable across MS Project builds and can abort the
    whole import/write-back if left uncaught. Prefer a best-effort close and
    always attempt ``Quit`` so zombie ``WINPROJ.EXE`` processes are not left
    behind.
    """
    try:
        app.FileCloseEx(0)
    except Exception:
        try:
            app.FileClose(0)
        except Exception as exc:
            logger.warning("FileClose fallito: %s", exc)
    try:
        app.Quit()
    except Exception as exc:
        logger.warning("Quit fallito: %s", exc)


def _to_msp_start_datetime(d: date) -> datetime:
    """Convert a calendar start date to a COM datetime at 09:00."""
    return datetime(d.year, d.month, d.day, _MSP_START_HOUR, 0)


def _to_msp_finish_datetime(d: date) -> datetime:
    """Convert a calendar finish date to a COM datetime at 18:00."""
    return datetime(d.year, d.month, d.day, _MSP_FINISH_HOUR, 0)


def _to_msp_clear_datetime(d: date) -> datetime:
    """Convert a sentinel date to midnight for clearing optional MS Project fields."""
    return datetime(d.year, d.month, d.day)


class Project:
    """A wrapper around the MS Project application to manage tasks and resources."""

    def __init__(self, path: str):
        """
        Args:
            path (str): The file path to the MS Project file to open.
        """
        app = _dispatch_msp()
        app.Visible = False
        try:
            app.FileOpen(os.path.abspath(path))
            project = app.ActiveProject
        except Exception:
            # Ensure the COM server is released if anything after Dispatch fails,
            # so we don't leave a zombie WINPROJ.EXE that would cause
            # CO_E_SERVER_EXEC_FAILURE on the next import attempt.
            try:
                app.Quit()
            except Exception:
                pass
            raise
        if project.ReadOnly:
            _safe_close_and_quit(app)
            raise PermissionError(
                f"Il file '{path}' è aperto in un'altra applicazione ed è in sola lettura. "
                "Chiuderlo prima di procedere."
            )
        self.app = app
        self.project = project

    def __enter__(self) -> "Project":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Save and close on exit; skip save if an exception occurred."""
        self.close(save=exc_type is None)

    @classmethod
    def from_open_app(cls, app, project_com: object) -> "Project":
        """Create a Project wrapper around an already-open COM session.

        Use this instead of ``__new__`` when you need to wrap a COM session
        that was opened externally (e.g. by :mod:`ui.services.project_session`).
        The returned instance shares ``app`` and ``project_com`` but does NOT
        own the lifecycle — calling :meth:`close` is the caller's responsibility.

        Args:
            app: An open ``MSProject.Application`` COM object.
            project_com: The ``ActiveProject`` COM object returned by the app.

        Returns:
            Project: A wrapper instance ready to call ``add_task`` etc.
        """
        instance = cls.__new__(cls)
        instance.app = app
        instance.project = project_com
        return instance

    def add_task(self, task: Task) -> None:
        """Append a task to the project and assign any listed resources.

        Args:
            task: The task to be added to the project.
        """
        t = self.project.Tasks.Add(task.name)

        t.Start = _to_msp_start_datetime(task.start)
        t.Finish = _to_msp_finish_datetime(task.end)

        if task.predecessors:
            t.Predecessors = task.predecessors
        if task.actual_start:
            t.Start1 = _to_msp_start_datetime(task.actual_start)
        if task.actual_end:
            t.Finish1 = _to_msp_finish_datetime(task.actual_end)

        t.Text1 = task.plant or ""
        t.Text2 = task.owner or ""
        t.Text3 = task.modello_testa or ""
        if task.bore_min is not None:
            t.Number1 = task.bore_min
        if task.bore_mid is not None:
            t.Number2 = task.bore_mid
        if task.bore_max is not None:
            t.Number3 = task.bore_max
        if task.tube_pitch is not None:
            t.Number4 = task.tube_pitch

        if task.resources:
            self.assign_resources(task.name, task.resources)

    def add_resource(self, data: Resource) -> None:
        """Append a resource to the project.

        Args:
            data: The resource to be added to the project.
        """
        r = self.project.Resources.Add(data.name)

        r.Text3 = data.measure
        r.Text1 = data.plant or ""
        r.Notes = data.notes or ""
        r.Text2 = data.category or ""
        r.Flag1 = bool(data.in_manutenzione)

    def list_tasks(self) -> list[Task]:
        """Return all tasks currently present in the project.

        Returns:
            A list of :class:`Task` instances, one per project task.
        """
        return [self._task_from_com(t) for t in self.project.Tasks if t is not None]

    def list_tasks_with_ids(self) -> list[tuple[int, Task]]:
        """Return all tasks together with their MS Project row IDs.

        MS Project assigns each task a unique integer ``ID`` that is used as a
        row reference in predecessor strings (e.g. ``"5"`` or ``"5FS+1d"``).
        This method exposes those IDs alongside the task data so that callers
        can build a mapping from predecessor row IDs to canonical identifiers.

        Returns:
            list[tuple[int, Task]]: Each element is ``(row_id, task)`` where
            *row_id* is the MSProject ``Task.ID`` integer.
        """
        return [
            (t.ID, self._task_from_com(t)) for t in self.project.Tasks if t is not None
        ]

    def list_resources(self) -> list[Resource]:
        """Return all resources currently present in the project.

        Returns:
            A list of :class:`Resource` instances, one per project resource.
        """
        return [
            self._resource_from_com(r) for r in self.project.Resources if r is not None
        ]

    def update_task(
        self,
        name: str,
        *,
        new_name: str | None = None,
        start: date | None = None,
        end: date | None = None,
        predecessors: str | None = None,
        actual_start: date | None = _UNSET,
        actual_end: date | None = _UNSET,
        plant: str | None = None,
        owner: str | None = None,
        modello_testa: str | None = None,
        bore_min: float | None = None,
        bore_mid: float | None = None,
        bore_max: float | None = None,
        tube_pitch: float | None = None,
    ) -> None:
        """Partially update an existing task.

        Only the keyword arguments explicitly passed are written to MS Project;
        all other fields remain unchanged.

        Args:
            name: Current name of the task to update.
            new_name: Rename the task to this value.
            start: New start date.
            end: New end date.
            predecessors: New predecessors string (e.g. ``'1,2FS+5d'``).
            actual_start: New actual start date (Start1), or ``None`` to clear.
                Omit (default) to leave the field unchanged.
            actual_end: New actual end date (Finish1), or ``None`` to clear.
                Omit (default) to leave the field unchanged.
            plant: New plant name (Text1).
            owner: New responsible person (Text2).
            modello_testa: New testa model name (Text3).
            bore_min: New minimum bore diameter (Number1).
            bore_mid: New mean bore diameter (Number2).
            bore_max: New maximum bore diameter (Number3).
            tube_pitch: New tube pitch / distanza interasse (Number4).

        Raises:
            ValueError: If no task with *name* is found.
        """
        t = self._find_task(name)
        if new_name is not None:
            t.Name = new_name
        if start is not None:
            t.Start = _to_msp_start_datetime(start)
        if end is not None:
            t.Finish = _to_msp_finish_datetime(end)
        if predecessors is not None:
            t.Predecessors = predecessors
        if actual_start is not _UNSET:
            t.Start1 = (
                _to_msp_start_datetime(actual_start)
                if actual_start is not None
                else _to_msp_clear_datetime(_MSP_CLEAR_DATE)
            )
        if actual_end is not _UNSET:
            t.Finish1 = (
                _to_msp_finish_datetime(actual_end)
                if actual_end is not None
                else _to_msp_clear_datetime(_MSP_CLEAR_DATE)
            )
        if plant is not None:
            t.Text1 = plant
        if owner is not None:
            t.Text2 = owner
        if modello_testa is not None:
            t.Text3 = modello_testa
        if bore_min is not None:
            t.Number1 = bore_min
        if bore_mid is not None:
            t.Number2 = bore_mid
        if bore_max is not None:
            t.Number3 = bore_max
        if tube_pitch is not None:
            t.Number4 = tube_pitch

    def update_resource(
        self,
        name: str,
        *,
        new_name: str | None = None,
        measure: int | float | None = None,
        plant: str | None = None,
        notes: str | None = None,
        category: str | None = None,
        in_manutenzione=_UNSET,
    ) -> None:
        """Partially update an existing resource.

        Only the keyword arguments explicitly passed are written to MS Project;
        all other fields remain unchanged.

        Args:
            name: Current name of the resource to update.
            new_name: Rename the resource to this value.
            measure: New measure value (Number1).
            plant: New plant name (Text1).
            notes: New notes.
            category: New category (Text2).
            in_manutenzione: New maintenance flag (Flag1). Omit to leave unchanged.

        Raises:
            ValueError: If no resource with *name* is found.
        """
        r = self._find_resource(name)
        if new_name is not None:
            r.Name = new_name
        if measure is not None:
            r.Text3 = measure
        if plant is not None:
            r.Text1 = plant
        if notes is not None:
            r.Notes = notes
        if category is not None:
            r.Text2 = category
        if in_manutenzione is not _UNSET:
            r.Flag1 = bool(in_manutenzione)

    def delete_task(self, name: str) -> None:
        """Delete a task from the project by name.

        Args:
            name: The name of the task to delete.

        Raises:
            ValueError: If no task with *name* is found.
        """
        self._find_task(name).Delete()

    def delete_resource(self, name: str) -> None:
        """Delete a resource from the project by name.

        Args:
            name: The name of the resource to delete.

        Raises:
            ValueError: If no resource with *name* is found.
        """
        self._find_resource(name).Delete()

    def save(self) -> None:
        """Save the project file to disk."""
        self.app.FileSave()

    def close(self, save: bool = True) -> None:
        """Close the project and quit the MS Project application.

        Args:
            save: If True (default), saves the file before closing.

        Notes:
            Close/Quit failures are logged and swallowed so callers (import and
            write-back) are not aborted after data has already been read or
            written. Save failures still propagate when ``save=True``.
        """
        if save:
            self.app.FileSave()
        _safe_close_and_quit(self.app)

    def get_task_id(self, name: str) -> Optional[int]:
        """Return the MS Project ID of a task by name, or None if not found.

        Args:
            name: The name of the task to find.

        Returns:
            The integer task ID, or None.
        """
        t = self._find_task_or_none(name)
        return t.ID if t is not None else None

    def get_resource_id(self, name: str) -> Optional[int]:
        """Return the MS Project ID of a resource by name, or None if not found.

        Args:
            name: The name of the resource to find.

        Returns:
            The integer resource ID, or None.
        """
        r = self._find_resource_or_none(name)
        return r.ID if r is not None else None

    def assign_resources(self, task: str, resources: list[str]) -> None:
        """Assign resources to a task.

        Args:
            task: The name of the task to which resources will be assigned.
            resources: A list of resource names to assign to the task.

        Raises:
            ValueError: If the task or any resource is not found.
        """
        task_obj = self._find_task(task)
        for resource_name in resources:
            resource_id = self.get_resource_id(resource_name)
            if resource_id is None:
                raise ValueError(
                    f"Resource '{resource_name}' not found in the project."
                )
            task_obj.Assignments.Add(task_obj.ID, resource_id)

    def set_task_resources(self, task: str, resources: list[str]) -> None:
        """Replace all resource assignments on a task.

        Existing assignments are removed before the new list is applied.

        Args:
            task: The name of the task to update.
            resources: Resource names to assign (may be empty to clear all).

        Raises:
            ValueError: If the task or any resource is not found.
        """
        task_obj = self._find_task(task)
        while task_obj.Assignments.Count > 0:
            task_obj.Assignments(task_obj.Assignments.Count).Delete()
        if resources:
            self.assign_resources(task, resources)

    def _find_task(self, name: str):
        """Return the COM task object for *name*, raising ValueError if absent."""
        t = self._find_task_or_none(name)
        if t is None:
            raise ValueError(f"Task '{name}' not found in the project.")
        return t

    def _find_task_or_none(self, name: str):
        """Return the COM task object for *name*, or None."""
        return next(
            (t for t in self.project.Tasks if t is not None and t.Name == name),
            None,
        )

    def _find_resource(self, name: str):
        """Return the COM resource object for *name*, raising ValueError if absent."""
        r = self._find_resource_or_none(name)
        if r is None:
            raise ValueError(f"Resource '{name}' not found in the project.")
        return r

    def _find_resource_or_none(self, name: str):
        """Return the COM resource object for *name*, or None."""
        return next(
            (r for r in self.project.Resources if r is not None and r.Name == name),
            None,
        )

    def _task_from_com(self, t) -> Task:
        """Build a :class:`Task` from a COM task object, bypassing name validation."""
        return Task.model_construct(
            name=t.Name,
            start=_to_date(t.Start),
            end=_to_date(t.Finish),
            predecessors=t.Predecessors or None,
            actual_start=_to_optional_date(t.Start1),
            actual_end=_to_optional_date(t.Finish1),
            plant=t.Text1 or None,
            owner=t.Text2 or None,
            modello_testa=t.Text3 or None,
            bore_min=_to_optional_float(t.Number1),
            bore_mid=_to_optional_float(t.Number2),
            bore_max=_to_optional_float(t.Number3),
            tube_pitch=_to_optional_float(t.Number4),
            resources=self._read_assignments(t),
        )

    @staticmethod
    def _read_assignments(t) -> list[str]:
        """Return the names of all resources assigned to a COM task object."""
        return [
            a.ResourceName for a in t.Assignments if a is not None and a.ResourceName
        ]

    def _resource_from_com(self, r) -> Resource:
        """Build a :class:`Resource` from a COM resource object."""
        return Resource.model_construct(
            name=r.Name,
            measure=r.Text3 or "",
            plant=r.Text1 or None,
            notes=r.Notes or None,
            category=r.Text2 or None,
            in_manutenzione=bool(getattr(r, "Flag1", False)),
        )


def _to_date(value) -> Optional[date]:
    """Convert a COM date value to :class:`date`.

    Handles four cases returned by the MS Project COM layer:

    - ``pywintypes.datetime`` / ``datetime.datetime`` objects (normal case)
    - Plain ``datetime.date`` objects (already the correct type)
    - ISO-format strings (observed in some locale/version combinations)
    - Non-date strings such as ``'N.D.'`` (Italian sentinel for "not set") → ``None``

    Args:
        value: The raw date value from a COM task or resource field.

    Returns:
        Optional[date]: A :class:`datetime.date` instance, or ``None`` if the
        value is a non-parseable sentinel string.
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if hasattr(value, "date"):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return value


def _to_optional_date(value) -> Optional[date]:
    """Convert a COM date value to :class:`date`, returning None for sentinel values."""
    if value is None:
        return None
    d = _to_date(value)
    # MS Project uses dates before _MSP_SENTINEL_YEAR as "not set" sentinels.
    if isinstance(d, date) and d.year < _MSP_SENTINEL_YEAR:
        return None
    return d


def _to_optional_float(value) -> Optional[float]:
    """Convert a COM Number field value to float, returning None for zero (unset sentinel).

    MS Project returns 0.0 for Number fields that have never been set.  For
    bore diameters and tube pitch this is safe because physical values are
    always strictly positive — zero is not a valid measurement in this domain.

    Args:
        value: Raw value from a COM Number field.

    Returns:
        Optional[float]: The float value, or None if falsy/zero.
    """
    if value is None or value == 0.0:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
