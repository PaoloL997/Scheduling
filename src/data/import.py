"""Import data from MS Project (.mpp) and Excel (.xlsx) into Django ORM.

Public API:
- :func:`import_from_mpp` — reads tasks and resources from an MS Project file
- :func:`import_from_xlsx` — reads job metadata from ``jobs.xlsx``

Both functions write directly to the Django ORM models
:class:`~ui.models.Attivita`, :class:`~ui.models.RisorsaAttivita`,
:class:`~ui.models.Job`, and the five inventory item models.  Callers must
ensure that :func:`~ui.services.reset.reset_app_dataset` has already cleared
those tables before calling these functions (the import pipeline in
:mod:`ui.services.importer` guarantees this via
:func:`~ui.services.reset.reset_app_dataset` before every
:func:`~ui.services.importer.trigger_reimport` call).
"""

import logging
from datetime import date, datetime
from typing import Optional

from src.data.models import Resource, Task
from src.data.project import Project
from src.data.schema import (
    GeneratoreRow,
    MandrinoRow,
    MascheraRow,
    TaskRow,
    TastatoreRow,
    TestaRow,
)

logger = logging.getLogger(__name__)

_CATEGORY_TO_TIPO: dict[str, str] = {
    "Testa": "testa",
    "Mandrino": "mandrino",
    "Tastatore": "tastatore",
    "Maschera": "maschera",
    "Generatore": "generatore",
}

# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------


def _parse_task_name(name: str) -> tuple[str, str, str]:
    """Split a task name into (commessa, item, activity).

    Supports both the canonical underscore format and the legacy hyphen format:

    - ``25033_01_qualifica`` → ``("25033", "01", "qualifica")``
    - ``25033_01``           → ``("25033", "01", "")``
    - ``25033-01-qualifica`` → ``("25033", "01", "qualifica")`` (legacy)
    - ``25033``              → ``("25033", "", "")``

    The underscore format is tried first; if no underscore is present the
    hyphen is used as a fallback for backward compatibility with old MPP files.

    Args:
        name: The full task name from MS Project.

    Returns:
        A 3-tuple ``(commessa, item, activity)``.
    """
    sep = "_" if "_" in name else "-"
    parts = name.split(sep, 2)
    commessa = parts[0] if len(parts) >= 1 else name
    item = parts[1] if len(parts) >= 2 else ""
    activity = parts[2] if len(parts) >= 3 else ""
    return commessa, item, activity


def _iso(d: Optional[date]) -> Optional[str]:
    """Return an ISO-8601 date string (``YYYY-MM-DD``), or None."""
    return d.isoformat() if d is not None else None


def _parse_date(val: Optional[str]) -> Optional[date]:
    """Parse an ISO-8601 date string, returning None if absent or malformed.

    Args:
        val: Date string in ``YYYY-MM-DD`` format, or None.

    Returns:
        A :class:`datetime.date` instance, or None.
    """
    if not val:
        return None
    try:
        return date.fromisoformat(val)
    except ValueError:
        return None


def _duration_min(start: date, end: date) -> int:
    """Return the duration in minutes, assuming 8-hour working days."""
    return (end - start).days * 8 * 60


def _convert_mpp_predecessors(
    predecessor_str: str,
    row_id_to_activity_id: dict[int, str],
) -> str | None:
    """Convert an MS Project predecessor string to a comma-separated activity_id list.

    MS Project stores predecessor references as semicolon-separated tokens such
    as ``"5"``, ``"5FS"``, or ``"5FS+1d"``.  This function extracts the leading
    integer row IDs from each token, resolves them against a pre-built lookup
    map, and returns the corresponding activity_ids separated by commas — the
    canonical format used in :attr:`~ui.models.Attivita.predecessors`.

    Tokens whose row ID is not present in the map (e.g. summary/milestone rows
    that were skipped during import) are silently dropped.

    Dependency-type suffixes (``FS``, ``FF``, ``SS``, ``SF``) and lag expressions
    (``+1d``, ``-2h``) are intentionally discarded because the planning table only
    needs the predecessor identity, not the scheduling constraint details.

    Args:
        predecessor_str: Raw MSProject predecessors string, e.g. ``"5;15FS+1d"``.
        row_id_to_activity_id: Mapping from MSProject row ID to canonical
            ``activity_id`` string built during the import pass.

    Returns:
        str | None: Comma-separated activity_ids, or ``None`` when no valid
        predecessors were resolved.
    """
    import re

    if not predecessor_str:
        return None

    resolved = []
    for token in predecessor_str.split(";"):
        token = token.strip()
        match = re.match(r"^(\d+)", token)
        if not match:
            continue
        row_id = int(match.group(1))
        activity_id = row_id_to_activity_id.get(row_id)
        if activity_id:
            resolved.append(activity_id)

    return ", ".join(resolved) if resolved else None


def _task_to_row(task: Task) -> TaskRow:
    """Convert a :class:`Task` Pydantic model to a :class:`TaskRow` dataclass.

    Args:
        task: A task returned by :meth:`Project.list_tasks`.

    Returns:
        A :class:`TaskRow` ready to be converted to an ORM instance.
    """
    commessa, item, activity = _parse_task_name(task.name)
    return TaskRow(
        commessa=commessa,
        item=item,
        activity=activity,
        duration_min=_duration_min(task.start, task.end),
        start=_iso(task.start),
        finish=_iso(task.end),
        actual_start=_iso(task.actual_start),
        actual_finish=_iso(task.actual_end),
        baseline_start=None,
        baseline_finish=None,
        plant=task.plant,
        responsible=task.owner,
        resources=";".join(task.resources) if task.resources else None,
        predecessors=task.predecessors,
        modello_testa=task.modello_testa,
        bore_min=task.bore_min,
        bore_mid=task.bore_mid,
        bore_max=task.bore_max,
        tube_pitch=task.tube_pitch,
    )


# ---------------------------------------------------------------------------
# Resource helpers
# ---------------------------------------------------------------------------


def _resource_to_table(resource: Resource) -> Optional[str]:
    """Return the target table name for a resource based on its category.

    Args:
        resource: A resource returned by :meth:`Project.list_resources`.

    Returns:
        The table name string, or ``None`` if the category is unknown.
    """
    return {
        "Testa": "Testa",
        "Mandrino": "Mandrino",
        "Tastatore": "Tastatore",
        "Maschera": "Maschera",
        "Generatore": "Generatore",
    }.get(resource.category or "")


def _resource_to_testa(r: Resource) -> TestaRow:
    return TestaRow(uid=r.name, model=r.measure or None, plant=r.plant, notes=r.notes)


def _resource_to_mandrino(r: Resource) -> MandrinoRow:
    return MandrinoRow(
        uid=r.name, diameter=r.measure or None, plant=r.plant, notes=r.notes
    )


def _resource_to_tastatore(r: Resource) -> TastatoreRow:
    return TastatoreRow(
        uid=r.name, diameter=r.measure or None, plant=r.plant, notes=r.notes
    )


def _resource_to_maschera(r: Resource) -> MascheraRow:
    return MascheraRow(
        uid=r.name, pitch=r.measure or None, plant=r.plant, center_distance=r.notes
    )


def _resource_to_generatore(r: Resource) -> GeneratoreRow:
    return GeneratoreRow(
        uid=r.name, model=r.measure or None, plant=r.plant, notes=r.notes
    )


_RESOURCE_CONVERTERS = {
    "Testa": _resource_to_testa,
    "Mandrino": _resource_to_mandrino,
    "Tastatore": _resource_to_tastatore,
    "Maschera": _resource_to_maschera,
    "Generatore": _resource_to_generatore,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def import_from_mpp(mpp_path: str) -> None:
    """Import tasks and resources from an MS Project file into the Django ORM.

    Opens the ``.mpp`` file via the MS Project COM API, reads all tasks and
    resources, then bulk-inserts them into :class:`~ui.models.Attivita`,
    :class:`~ui.models.RisorsaAttivita`, and the five inventory item models.
    The tables are assumed to be empty at call time (ensured by
    :func:`~ui.services.reset.reset_app_dataset`).

    Tasks with an empty ``item`` segment (e.g. MS Project summary rows) are
    silently skipped. Item values are preserved as strings.

    The file is opened in read-only mode (never saved) so that the import
    does not leave a dirty modification timestamp on the ``.mpp``.

    Args:
        mpp_path: Path to the ``.mpp`` MS Project file.
    """
    from django.db import close_old_connections
    from ui.models import (
        Attivita,
        RisorsaAttivita,
        GeneratoreItem,
        MandrinoItem,
        MascheraItem,
        TastatoreItem,
        TestaItem,
    )
    from ui.services.identifiers import build_activity_id, build_job_id

    project = Project(mpp_path)
    try:
        tasks_with_ids = project.list_tasks_with_ids()
        resources = project.list_resources()
    finally:
        project.close(save=False)

    uid_to_tipo: dict[str, str] = {}
    for r in resources:
        tipo = _CATEGORY_TO_TIPO.get(r.category or "")
        if tipo:
            uid_to_tipo[r.name] = tipo

    row_id_to_activity_id: dict[int, str] = {}
    for row_id, task in tasks_with_ids:
        r = _task_to_row(task)
        item = (r.item or "").strip()
        if not item:
            continue
        row_id_to_activity_id[row_id] = build_activity_id(
            r.commessa, item, r.activity
        )

    attivita_objects: list[Attivita] = []
    task_resource_map: dict[tuple, list[str]] = {}

    for _row_id, task in tasks_with_ids:
        r = _task_to_row(task)
        item = (r.item or "").strip()
        if not item:
            continue

        resource_uids = [u for u in (r.resources or "").split(";") if u]
        key = (r.commessa, item, r.activity)
        if resource_uids:
            task_resource_map[key] = resource_uids

        attivita_objects.append(
            Attivita(
                commessa=r.commessa,
                item=item,
                activity=r.activity,
                job_id=build_job_id(r.commessa),
                activity_id=build_activity_id(r.commessa, item, r.activity),
                duration_min=r.duration_min,
                data_inizio=_parse_date(r.start),
                data_fine=_parse_date(r.finish),
                actual_start=_parse_date(r.actual_start),
                actual_finish=_parse_date(r.actual_finish),
                baseline_start=_parse_date(r.baseline_start),
                baseline_finish=_parse_date(r.baseline_finish),
                stabilimento=r.plant or "",
                responsabile=r.responsible or "",
                predecessors=_convert_mpp_predecessors(
                    r.predecessors or "", row_id_to_activity_id
                ),
                modello_testa=r.modello_testa or "",
                bore_min=r.bore_min,
                bore_mid=r.bore_mid,
                bore_max=r.bore_max,
                tube_pitch=r.tube_pitch,
                imported_from_mpp=True,
                synced_to_mpp=True,
            )
        )

    Attivita.objects.bulk_create(attivita_objects, ignore_conflicts=True)

    if task_resource_map:
        attivita_by_key = {
            (a["commessa"], a["item"], a["activity"]): a["id"]
            for a in Attivita.objects.filter(imported_from_mpp=True).values(
                "id", "commessa", "item", "activity"
            )
        }
        risorsa_objects: list[RisorsaAttivita] = []
        for key, uids in task_resource_map.items():
            att_pk = attivita_by_key.get(key)
            if not att_pk:
                continue
            for uid in uids:
                tipo = uid_to_tipo.get(uid)
                if tipo:
                    risorsa_objects.append(
                        RisorsaAttivita(attivita_id=att_pk, tipo=tipo, risorsa_id=uid)
                    )
        RisorsaAttivita.objects.bulk_create(risorsa_objects, ignore_conflicts=True)

    testas, mandrini, tastatori, maschere, generatori = [], [], [], [], []
    for resource in resources:
        cat = resource.category or ""
        conv = _RESOURCE_CONVERTERS.get(cat)
        if conv is None:
            continue
        row = conv(resource)
        if cat == "Testa":
            testas.append(
                TestaItem(
                    uid=row.uid, model=row.model, plant=row.plant, notes=row.notes
                )
            )
        elif cat == "Mandrino":
            mandrini.append(
                MandrinoItem(
                    uid=row.uid, diameter=row.diameter, plant=row.plant, notes=row.notes
                )
            )
        elif cat == "Tastatore":
            tastatori.append(
                TastatoreItem(
                    uid=row.uid, diameter=row.diameter, plant=row.plant, notes=row.notes
                )
            )
        elif cat == "Maschera":
            maschere.append(
                MascheraItem(
                    uid=row.uid,
                    pitch=row.pitch,
                    plant=row.plant,
                    center_distance=row.center_distance,
                )
            )
        elif cat == "Generatore":
            generatori.append(
                GeneratoreItem(
                    uid=row.uid, model=row.model, plant=row.plant, notes=row.notes
                )
            )

    TestaItem.objects.bulk_create(testas, ignore_conflicts=True)
    MandrinoItem.objects.bulk_create(mandrini, ignore_conflicts=True)
    TastatoreItem.objects.bulk_create(tastatori, ignore_conflicts=True)
    MascheraItem.objects.bulk_create(maschere, ignore_conflicts=True)
    GeneratoreItem.objects.bulk_create(generatori, ignore_conflicts=True)

    close_old_connections()


def import_from_xlsx(xlsx_path: str) -> None:
    """Import job metadata from an Excel file into the :class:`~ui.models.Job` table.

    Reads ``Sheet1`` of the workbook.  Expected columns (in order):
    ``Job ID``, ``Commessa``, ``Costo Totale``, ``Penalità``, ``Data consegna prevista``.

    The table is assumed to be empty at call time (ensured by
    :func:`~ui.services.reset.reset_app_dataset`).

    Args:
        xlsx_path: Path to the ``.xlsx`` Excel file.
    """
    import openpyxl

    from django.db import close_old_connections
    from ui.models import Job
    from ui.services.identifiers import build_job_id

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["Sheet1"]

    job_objects = []
    for excel_row in ws.iter_rows(min_row=2, values_only=True):
        if len(excel_row) < 5:
            continue
        _job_id_raw, commessa_raw, total_cost, penalty, delivery_raw = excel_row[:5]
        if commessa_raw is None:
            continue
        commessa_val = str(commessa_raw).strip()
        job_objects.append(
            Job(
                commessa=commessa_val,
                job_id=build_job_id(commessa_val),
                total_cost=_safe_float(total_cost),
                penalty=_safe_float(penalty),
                delivery_date=_extract_delivery_date(delivery_raw),
            )
        )

    Job.objects.bulk_create(job_objects, ignore_conflicts=True)
    close_old_connections()


def _safe_float(value) -> Optional[float]:
    """Coerce an Excel cell value to float, returning None when not numeric.

    Accepts plain numeric types directly.  For strings it strips whitespace and
    common formatting characters (currency symbols, thousand separators) before
    attempting conversion.  Any value that cannot be converted is silently
    discarded so that a misplaced text cell (e.g. a commessa string like
    ``"25079-01"`` in a cost column) never aborts the full import.

    Args:
        value: The raw cell value from openpyxl — may be int, float, str, or None.

    Returns:
        A Python float, or None if the value is absent or non-numeric.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace("€", "").replace("$", "").replace(" ", "")
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        logger.warning(
            "import_from_xlsx: valore non numerico ignorato nella colonna costo/penale: %r",
            value,
        )
        return None


def _extract_delivery_date(value) -> Optional[str]:
    """Convert an Excel date value to an ISO-8601 string.

    Handles ``datetime`` objects (openpyxl default), plain ``date`` objects,
    and ISO-format strings.  Returns ``None`` for missing or unparseable values.

    Args:
        value: The raw cell value from openpyxl.

    Returns:
        An ISO-8601 date string (``YYYY-MM-DD``), or ``None``.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return datetime.fromisoformat(str(value)).date().isoformat()
    except ValueError:
        return None
