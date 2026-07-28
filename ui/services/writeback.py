"""Write-back service: apply MppSyncQueue entries to the open MS Project COM session.

Called by :mod:`ui.services.project_session` when the last authenticated user
logs out.  All pending queue entries are processed in creation order, then
deleted.  Any individual entry that fails is logged but does not prevent the
remaining entries from being processed.

Job-related operations (``add_job``, ``update_job``, ``delete_job``) are skipped
here because the Excel writeback (Phase D) performs a full overwrite of the jobs
sheet directly from the current :class:`~ui.models.Job` table, making per-change
queuing unnecessary.
"""

import json
import logging
from datetime import date

from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

logger = logging.getLogger(__name__)

_JOB_OPS = frozenset({"add_job", "update_job", "delete_job"})


def write_pending_changes(project_wrapper) -> int:
    """Apply all pending :class:`~ui.models.MppSyncQueue` entries to the project.

    Processes queue entries in ``created_at`` order (oldest first).  Entries are
    **not** deleted here: the caller must clear the queue only after
    ``FileSave`` and the Excel export both succeed.  If any entry fails, this
    function raises and leaves the entire queue intact so a retry can re-apply
    from a freshly opened (unsaved) COM session.

    After a task is added, the corresponding :class:`~ui.models.Attivita`
    record is marked ``synced_to_mpp=True`` to remove it from the live merge
    on the pianificazione page.

    Job operations (``add_job``, ``update_job``, ``delete_job``) are no-ops for
    the ``.mpp`` file; the Excel file is updated separately via the
    full-overwrite export in Phase D.

    Args:
        project_wrapper: An open :class:`~src.data.project.Project` instance
            that wraps the COM session.  Must not be closed before this
            function returns.

    Returns:
        Number of queue entries successfully applied (or skipped as no-ops).

    Raises:
        RuntimeError: If one or more entries could not be applied.  No entries
            are deleted from the queue in that case.
    """
    from ui.models import MppSyncQueue

    entries = list(MppSyncQueue.objects.order_by("created_at"))
    if not entries:
        logger.info("MppSyncQueue vuota, nessuna modifica da scrivere.")
        return 0

    logger.info("Scrittura di %d voci su .mpp...", len(entries))
    total = len(entries)
    failures = 0
    applied = 0
    with logging_redirect_tqdm():
        with tqdm(
            entries, desc="Scrittura .mpp", unit="op", ncols=72, leave=True
        ) as pbar:
            for entry in pbar:
                try:
                    payload = json.loads(entry.payload_json)
                    op = entry.operation
                    pbar.set_postfix_str(op, refresh=False)
                    if op == "add_task":
                        _apply_add_task(project_wrapper, payload)
                    elif op == "update_task":
                        _apply_update_task(project_wrapper, payload)
                    elif op == "delete_task":
                        _apply_delete_task(project_wrapper, payload)
                    elif op == "add_resource":
                        _apply_add_resource(project_wrapper, payload)
                    elif op == "update_resource":
                        _apply_update_resource(project_wrapper, payload)
                    elif op == "delete_resource":
                        _apply_delete_resource(project_wrapper, payload)
                    elif op in _JOB_OPS:
                        pass  # no-op su .mpp; xlsx usa full-overwrite
                    else:
                        logger.warning("Operazione sconosciuta nella coda: %s", op)
                    applied += 1
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    logger.error(
                        "Errore applicando MppSyncQueue entry %s (%s): %s",
                        entry.pk,
                        entry.operation,
                        exc,
                    )

    if failures:
        raise RuntimeError(
            f"Write-back incompleto: {failures}/{total} operazioni non applicate. "
            "La coda resta intatta e il dataset non verrà resettato."
        )

    logger.info(
        "Scrittura .mpp completata (%d voci, coda non ancora cancellata).", applied
    )
    return applied


# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------


def _format_mpp_item(item: int | str) -> str:
    """Normalize an item for MS Project task names.

    Numeric items keep the historical zero-padded form (``1`` → ``01``) so
    existing ``.mpp`` tasks remain matchable. Non-numeric items are kept as-is.
    """
    text = str(item).strip()
    if text.isdigit():
        return f"{int(text):02d}"
    return text


def _task_mpp_name(commessa: str, item: int | str, activity: str) -> str:
    """Build the canonical MS Project task name using underscore separators.

    The canonical format is ``{commessa}_{item}_{activity}`` when an
    activity is present, or ``{commessa}_{item}`` otherwise.

    Args:
        commessa: The job identifier (e.g. ``"25033"``).
        item: The item identifier.
        activity: The activity label, or an empty string.

    Returns:
        str: The canonical task name.
    """
    item_txt = _format_mpp_item(item)
    if activity:
        return f"{commessa}_{item_txt}_{activity}"
    return f"{commessa}_{item_txt}"


def _find_mpp_task(project_wrapper, commessa: str, item: int | str, activity: str):
    """Locate a COM task object, trying canonical (``_``) and legacy (``-``) formats.

    MS Project files created before the underscore-separator convention used
    hyphens.  This function tries the current canonical format first, then
    falls back to the legacy format so that old files remain writable.

    Args:
        project_wrapper: Open :class:`~src.data.project.Project` instance.
        commessa: The job identifier.
        item: The item identifier.
        activity: The activity label, or an empty string.

    Returns:
        tuple[object, str]: The COM task object and the name that matched.

    Raises:
        ValueError: If the task is not found under either name format.
    """
    candidates: list[str] = []
    item_txt = str(item).strip()
    formatted = _format_mpp_item(item)
    for item_variant in dict.fromkeys([formatted, item_txt]):
        name_new = (
            f"{commessa}_{item_variant}_{activity}"
            if activity
            else f"{commessa}_{item_variant}"
        )
        candidates.append(name_new)
        name_legacy = (
            f"{commessa}-{item_variant}-{activity}"
            if activity
            else f"{commessa}-{item_variant}"
        )
        candidates.append(name_legacy)

    for name in candidates:
        com_task = project_wrapper._find_task_or_none(name)
        if com_task is not None:
            return com_task, name

    raise ValueError(f"Task '{candidates[0]}' (o varianti) non trovato nel file .mpp.")


# ---------------------------------------------------------------------------
# Task apply handlers
# ---------------------------------------------------------------------------


def _activity_ids_to_mpp_predecessors(
    project_wrapper, predecessor_str: str
) -> str | None:
    """Convert a comma-separated list of DB activity_ids to MSProject row IDs.

    Activity identifiers stored in the database use the canonical
    ``commessa-item-activity`` format (URL-encoded, dash-separated), whereas
    MS Project requires predecessor references as semicolon-separated numeric
    row IDs.  This function translates between the two representations by
    looking up each activity's MPP task name and retrieving its ``ID`` from
    the open project.

    Any activity_id that cannot be parsed or whose corresponding task cannot
    be located in the project is silently skipped with a warning log, so a
    partial result is returned rather than failing the whole operation.

    Args:
        project_wrapper: Open :class:`~src.data.project.Project` COM wrapper.
        predecessor_str: Comma-separated activity_ids, e.g. ``"25079-01-SNA, 25079-02-prep"``.

    Returns:
        str | None: Semicolon-separated MSProject row IDs (e.g. ``"5;12"``),
        or ``None`` when no valid predecessors were resolved.
    """
    if not predecessor_str:
        return None

    from ui.services.identifiers import parse_activity_id

    row_ids = []
    for raw in predecessor_str.split(","):
        activity_id = raw.strip()
        if not activity_id:
            continue
        try:
            commessa, item, activity = parse_activity_id(activity_id)
        except ValueError:
            logger.warning("activity_id predecessore non valido: %s", activity_id)
            continue

        try:
            com_task, mpp_name = _find_mpp_task(
                project_wrapper, commessa, item, activity
            )
        except ValueError:
            logger.warning(
                "Predecessore '%s' non trovato nel .mpp.",
                activity_id,
            )
            continue
        row_ids.append(str(com_task.ID))

    return ";".join(row_ids) if row_ids else None


def _apply_add_task(project_wrapper, payload: dict) -> None:
    """Add a new task to the MS Project file from an ``add_task`` queue entry.

    Args:
        project_wrapper: Open Project COM wrapper.
        payload: Decoded JSON dict with task fields.
    """
    import importlib

    src_models = importlib.import_module("src.data.models")
    Task = src_models.Task

    commessa = payload["commessa"]
    item = payload["item"]
    activity = payload.get("activity", "")
    name = _task_mpp_name(commessa, item, activity)

    # Idempotent: a retry after FileSave must not create duplicates.
    try:
        _find_mpp_task(project_wrapper, commessa, item, activity)
    except ValueError:
        pass
    else:
        logger.debug("Task '%s' già presente nel .mpp, skip add_task.", name)
        attivita_id = payload.get("attivita_id")
        if attivita_id:
            from ui.models import Attivita

            Attivita.objects.filter(pk=attivita_id).update(synced_to_mpp=True)
        return

    start = (
        date.fromisoformat(payload["data_inizio"])
        if payload.get("data_inizio")
        else None
    )
    end = date.fromisoformat(payload["data_fine"]) if payload.get("data_fine") else None

    mpp_predecessors = _activity_ids_to_mpp_predecessors(
        project_wrapper, payload.get("predecessors") or ""
    )

    task = Task(
        name=name,
        start=start,
        end=end,
        plant=payload.get("stabilimento") or None,
        owner=payload.get("responsabile") or None,
        resources=payload.get("resources") or [],
        predecessors=mpp_predecessors,
        actual_start=None,
        actual_end=None,
        modello_testa=payload.get("modello_testa") or None,
        bore_min=payload.get("bore_min"),
        bore_mid=payload.get("bore_mid"),
        bore_max=payload.get("bore_max"),
        tube_pitch=payload.get("tube_pitch"),
    )
    project_wrapper.add_task(task)
    logger.debug("Task aggiunto al .mpp: %s", name)

    attivita_id = payload.get("attivita_id")
    if attivita_id:
        from ui.models import Attivita

        Attivita.objects.filter(pk=attivita_id).update(synced_to_mpp=True)


def _apply_update_task(project_wrapper, payload: dict) -> None:
    """Update an existing task in the MS Project file from an ``update_task`` queue entry.

    Finds the task by trying the canonical ``_``-separated name first, then the
    legacy ``-``-separated name.  If the task was stored under the legacy format
    it is also renamed to the canonical format as a side effect.

    Args:
        project_wrapper: Open Project COM wrapper.
        payload: Decoded JSON dict with keys ``commessa``, ``item``, ``activity``
            and optional ``start``, ``finish``, ``actual_start``, ``actual_finish``,
            ``plant``, ``responsible``, ``resources``.
    """
    commessa = payload["commessa"]
    item = payload["item"]
    activity = payload.get("activity", "")

    start = date.fromisoformat(payload["start"]) if payload.get("start") else None
    finish = date.fromisoformat(payload["finish"]) if payload.get("finish") else None
    plant = payload.get("plant")
    owner = payload.get("responsible")

    _, current_name = _find_mpp_task(project_wrapper, commessa, item, activity)
    canonical_name = _task_mpp_name(commessa, item, activity)
    rename_to = canonical_name if current_name != canonical_name else None

    update_kwargs = {
        "new_name": rename_to,
        "start": start,
        "end": finish,
        "plant": plant,
        "owner": owner,
    }
    if "actual_start" in payload:
        update_kwargs["actual_start"] = (
            date.fromisoformat(payload["actual_start"])
            if payload.get("actual_start")
            else None
        )
    if "actual_finish" in payload:
        update_kwargs["actual_end"] = (
            date.fromisoformat(payload["actual_finish"])
            if payload.get("actual_finish")
            else None
        )

    project_wrapper.update_task(current_name, **update_kwargs)
    task_name = rename_to or current_name
    if "resources" in payload:
        project_wrapper.set_task_resources(task_name, payload.get("resources") or [])
    logger.debug("Task aggiornato nel .mpp: %s → %s", current_name, canonical_name)


def _apply_delete_task(project_wrapper, payload: dict) -> None:
    """Delete an existing task from the MS Project file from a ``delete_task`` queue entry.

    Finds the task by trying the canonical ``_``-separated name first, then the
    legacy ``-``-separated name.  Missing tasks are skipped (idempotent retry).

    Args:
        project_wrapper: Open Project COM wrapper.
        payload: Decoded JSON dict with keys ``commessa``, ``item``, ``activity``.
    """
    commessa = payload["commessa"]
    item = payload["item"]
    activity = payload.get("activity", "")

    try:
        _, current_name = _find_mpp_task(project_wrapper, commessa, item, activity)
    except ValueError:
        logger.debug(
            "Task '%s' già assente nel .mpp, skip delete_task.",
            _task_mpp_name(commessa, item, activity),
        )
        return
    project_wrapper.delete_task(current_name)
    logger.debug("Task eliminato dal .mpp: %s", current_name)


def _apply_add_resource(project_wrapper, payload: dict) -> None:
    """Add a new resource to the MS Project file from an ``add_resource`` queue entry.

    Skips the entry if the resource is already present in the ``.mpp`` file
    (idempotent, prevents duplicates when the same uid is enqueued more than once).

    Legacy bulk-import marker entries (``{"bulk_import": True, ...}``) are consumed
    without writing anything — they remain only for backward compatibility with
    queue entries created before individual enqueuing was introduced.

    Args:
        project_wrapper: Open Project COM wrapper.
        payload: Decoded JSON dict with resource fields, or a bulk-import marker.
    """
    if payload.get("bulk_import"):
        logger.info(
            "Bulk-import marker (%d risorse) consumato — nessuna azione richiesta.",
            payload.get("count", 0),
        )
        return

    import importlib

    src_models = importlib.import_module("src.data.models")
    Resource = src_models.Resource

    uid = payload["uid"]

    if project_wrapper._find_resource_or_none(uid) is not None:
        logger.debug("Risorsa '%s' già presente nel .mpp, skip.", uid)
        return

    resource = Resource(
        name=uid,
        category=payload.get("table") or "",
        measure=payload.get("measure") or "",
        plant=payload.get("plant") or None,
        notes=payload.get("notes") or None,
    )
    project_wrapper.add_resource(resource)


def _apply_update_resource(project_wrapper, payload: dict) -> None:
    """Update an existing resource in the MS Project file from an ``update_resource`` queue entry.

    Applies a partial update: only fields present in the payload overwrite the
    current values in the ``.mpp`` file.  If the ``uid`` has changed (rename),
    ``new_name`` is passed to the COM wrapper so the resource name is updated as well.

    If the resource is not found in the ``.mpp`` (e.g. it was never written there),
    the entry is skipped with a warning rather than raising an exception.

    Args:
        project_wrapper: Open Project COM wrapper.
        payload: Decoded JSON dict with keys ``original_uid``, ``uid``, ``table``,
            ``measure``, ``plant``, ``notes``.
    """
    original_uid = payload["original_uid"]
    new_uid = payload.get("uid") or original_uid
    rename_to = new_uid if new_uid != original_uid else None

    try:
        project_wrapper.update_resource(
            original_uid,
            new_name=rename_to,
            measure=payload.get("measure"),
            plant=payload.get("plant"),
            notes=payload.get("notes"),
            category=payload.get("table"),
        )
        logger.debug("Risorsa aggiornata nel .mpp: %s → %s", original_uid, new_uid)
    except ValueError:
        logger.warning(
            "Risorsa '%s' non trovata nel .mpp per aggiornamento, skip.",
            original_uid,
        )


def _apply_delete_resource(project_wrapper, payload: dict) -> None:
    """Delete a resource from the MS Project file from a ``delete_resource`` queue entry.

    Args:
        project_wrapper: Open Project COM wrapper.
        payload: Decoded JSON dict with ``uid`` key.
    """
    uid = payload["uid"]
    try:
        project_wrapper.delete_resource(uid)
        logger.debug("Risorsa eliminata dal .mpp: %s", uid)
    except ValueError:
        logger.warning("Risorsa '%s' non trovata nel .mpp, skip.", uid)
