import os
from datetime import date, datetime
from typing import Optional

import win32com.client

from src.data.models import Resource, Task

# MS Project sentinel date used for "not set" optional date fields.
_MSP_SENTINEL_YEAR = 1990


class Project:
    """A wrapper around the MS Project application to manage tasks and resources."""

    def __init__(self, path: str):
        """
        Args:
            path (str): The file path to the MS Project file to open.
        """
        self.app = win32com.client.Dispatch("MSProject.Application")
        self.app.Visible = False
        self.app.FileOpen(os.path.abspath(path))
        self.project = self.app.ActiveProject
        if self.project.ReadOnly:
            self.app.FileCloseEx(Save=0)
            self.app.Quit()
            raise PermissionError(
                f"Il file '{path}' è aperto in un'altra applicazione ed è in sola lettura. "
                "Chiuderlo prima di procedere."
            )

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

        t.Start = _to_msp_date(task.start)
        t.Finish = _to_msp_date(task.end)

        if task.predecessors:
            t.Predecessors = task.predecessors
        if task.actual_start:
            t.Start1 = _to_msp_date(task.actual_start)
        if task.actual_end:
            t.Finish1 = _to_msp_date(task.actual_end)

        t.Text1 = task.plant or ""
        t.Text2 = task.owner or ""

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

    def list_tasks(self) -> list[Task]:
        """Return all tasks currently present in the project.

        Returns:
            A list of :class:`Task` instances, one per project task.
        """
        return [self._task_from_com(t) for t in self.project.Tasks if t is not None]

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
        actual_start: date | None = None,
        actual_end: date | None = None,
        plant: str | None = None,
        owner: str | None = None,
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
            actual_start: New actual start date (Start1).
            actual_end: New actual end date (Finish1).
            plant: New plant name (Text1).
            owner: New responsible person (Text2).

        Raises:
            ValueError: If no task with *name* is found.
        """
        t = self._find_task(name)
        if new_name is not None:
            t.Name = new_name
        if start is not None:
            t.Start = _to_msp_date(start)
        if end is not None:
            t.Finish = _to_msp_date(end)
        if predecessors is not None:
            t.Predecessors = predecessors
        if actual_start is not None:
            t.Start1 = _to_msp_date(actual_start)
        if actual_end is not None:
            t.Finish1 = _to_msp_date(actual_end)
        if plant is not None:
            t.Text1 = plant
        if owner is not None:
            t.Text2 = owner

    def update_resource(
        self,
        name: str,
        *,
        new_name: str | None = None,
        measure: int | float | None = None,
        plant: str | None = None,
        notes: str | None = None,
        category: str | None = None,
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
        """
        if save:
            self.app.FileSave()
        self.app.FileCloseEx(Save=0)  # 0 = do not prompt, discard unsaved changes
        self.app.Quit()

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
        )


def _to_msp_date(d: date) -> Optional[datetime]:
    """Convert a :class:`date` to a :class:`datetime` for MS Project COM date fields.

    Args:
        d: The date to convert, or None.

    Returns:
        Optional[datetime]: A datetime instance, or None if d is None.
    """
    if d is None:
        return None
    return datetime(d.year, d.month, d.day)


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
