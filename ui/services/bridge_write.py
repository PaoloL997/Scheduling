"""Write operations shared by the web API and the tablet bridge."""

from __future__ import annotations

import datetime
import json
import logging

from django.db import transaction

from ui.models import Attivita, MppSyncQueue, RisorsaAttivita
from ui.services.resource_conflicts import (
    ResourceConflictError,
    format_conflict_message,
    validate_no_conflicts,
)
from ui.services.resource_display import (
    format_activity_resources,
    format_activity_resources_html,
)

logger = logging.getLogger(__name__)


def _risorse_to_pairs(risorse_data: dict) -> list[tuple[str, str]]:
    """Convert wizard-style risorse dict to (tipo, risorsa_id) pairs."""
    pairs: list[tuple[str, str]] = []
    for tipo, key in [
        ("testa", "teste"),
        ("mandrino", "mandrini"),
        ("tastatore", "tastatori"),
        ("maschera", "maschere"),
        ("generatore", "generatori"),
    ]:
        for rid in risorse_data.get(key, []) or []:
            if rid:
                pairs.append((tipo, str(rid)))
    return pairs


class BridgeWriteError(Exception):
    """Raised when a bridge write operation fails validation."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        conflicts: list | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.conflicts = conflicts


def _parse_date(val) -> datetime.date | None:
    if val:
        try:
            return datetime.date.fromisoformat(val)
        except ValueError:
            pass
    return None


def update_attivita(orm_id: int, data: dict, *, resolve_overlaps: bool = True) -> dict:
    """Update a single :class:`~ui.models.Attivita` and enqueue write-back.

    Args:
        orm_id: Primary key of the activity.
        data: Field updates (same keys as ``api_tasks_update``).
        resolve_overlaps: When True, auto-swap delay overlaps after the write.

    Returns:
        dict: Success payload with ``item``, ``job_id``, ``activity_id``.

    Raises:
        BridgeWriteError: On validation or not-found errors.
    """
    try:
        a = Attivita.objects.get(pk=orm_id)
    except Attivita.DoesNotExist as exc:
        raise BridgeWriteError("Attività non trovata.", status_code=404) from exc

    old_commessa = a.commessa
    old_item = a.item
    old_activity = a.activity

    if "commessa" in data:
        new_commessa = (data["commessa"] or "").strip()
        if new_commessa:
            a.commessa = new_commessa
    if "item" in data:
        item_value = str(data["item"]).strip()
        if not item_value:
            raise BridgeWriteError(
                "Il valore di item è obbligatorio.",
                status_code=400,
            )
        a.item = item_value
    if "activity" in data:
        from ui.services.activity_inventory import validate_activity_type

        try:
            a.activity = validate_activity_type(data["activity"])
        except ValueError as exc:
            raise BridgeWriteError(str(exc), status_code=400) from exc

    if a.commessa != old_commessa or a.item != old_item or a.activity != old_activity:
        if (
            Attivita.objects.filter(
                commessa=a.commessa, item=a.item, activity=a.activity
            )
            .exclude(pk=a.pk)
            .exists()
        ):
            raise BridgeWriteError(
                "Esiste già un'attività con questa combinazione "
                "commessa/item/attività.",
                status_code=409,
            )

    if "start" in data:
        a.data_inizio = _parse_date(data["start"])
    if "finish" in data:
        a.data_fine = _parse_date(data["finish"])
    if "actual_start" in data:
        a.actual_start = _parse_date(data["actual_start"])
    if "actual_finish" in data:
        a.actual_finish = _parse_date(data["actual_finish"])
    if "plant" in data:
        a.stabilimento = data["plant"] or ""
    if "responsible" in data:
        a.responsabile = data["responsible"] or ""
    if "predecessors" in data:
        predecessor_ids = [
            str(v).strip() for v in data["predecessors"] if str(v).strip()
        ]
        if predecessor_ids:
            valid = set(
                Attivita.objects.filter(commessa=a.commessa)
                .exclude(pk=a.pk)
                .values_list("activity_id", flat=True)
            )
            invalid = [p for p in predecessor_ids if p not in valid]
            if invalid:
                raise BridgeWriteError(
                    f"Predecessor non valido: {', '.join(invalid)}",
                    status_code=400,
                )
        a.predecessors = ", ".join(predecessor_ids) if predecessor_ids else None

    risorse_data = data.get("risorse")
    new_risorse_pairs: list[tuple[str, str]] | None = None
    if risorse_data is not None:
        if not isinstance(risorse_data, dict):
            raise BridgeWriteError("Formato risorse non valido.", status_code=400)
        new_risorse_pairs = _risorse_to_pairs(risorse_data)
        if a.data_inizio and a.data_fine:
            try:
                validate_no_conflicts(
                    new_risorse_pairs,
                    a.data_inizio,
                    a.data_fine,
                    exclude_activity_id=a.pk,
                )
            except ResourceConflictError as exc:
                raise BridgeWriteError(
                    format_conflict_message(
                        exc.conflicts,
                        action_hint=(
                            "Scegli risorse alternative non occupate "
                            "nel periodo indicato."
                        ),
                    ),
                    status_code=409,
                    conflicts=exc.conflicts,
                ) from exc
        elif new_risorse_pairs:
            raise BridgeWriteError(
                "Per assegnare risorse servono data di inizio e fine.",
                status_code=400,
            )

    if ("start" in data or "finish" in data) and a.data_inizio and a.data_fine:
        existing_pairs = list(
            RisorsaAttivita.objects.filter(attivita_id=a.pk).values_list(
                "tipo", "risorsa_id"
            )
        )
        try:
            validate_no_conflicts(
                existing_pairs,
                a.data_inizio,
                a.data_fine,
                exclude_activity_id=a.pk,
            )
        except ResourceConflictError as exc:
            raise BridgeWriteError(
                format_conflict_message(
                    exc.conflicts,
                    action_hint=(
                        "Usa «Modifica risorse» per sostituirle o rimuoverle, "
                        "oppure accorcia il periodo dell'attività."
                    ),
                ),
                status_code=409,
                conflicts=exc.conflicts,
            ) from exc

    with transaction.atomic():
        a.save()

        if new_risorse_pairs is not None:
            RisorsaAttivita.objects.filter(attivita_id=a.pk).delete()
            risorsa_rows = [
                RisorsaAttivita(attivita=a, tipo=tipo, risorsa_id=rid)
                for tipo, rid in new_risorse_pairs
            ]
            if risorsa_rows:
                RisorsaAttivita.objects.bulk_create(risorsa_rows)

    if risorse_data is not None:
        from ui.services.problem_detection import detect_and_save_problems

        detect_and_save_problems(a, risorse_data)
    elif "plant" in data:
        from ui.services.problem_detection import refresh_problems_for_attivita

        refresh_problems_for_attivita(a)

    mpp_payload: dict = {
        "attivita_id": orm_id,
        "commessa": a.commessa,
        "item": a.item,
        "activity": a.activity,
        "old_commessa": old_commessa,
        "old_item": old_item,
        "old_activity": old_activity,
        "start": a.data_inizio.isoformat() if a.data_inizio else None,
        "finish": a.data_fine.isoformat() if a.data_fine else None,
        "actual_start": a.actual_start.isoformat() if a.actual_start else None,
        "actual_finish": a.actual_finish.isoformat() if a.actual_finish else None,
        "plant": a.stabilimento or None,
        "responsible": a.responsabile or None,
    }
    if "predecessors" in data:
        mpp_payload["predecessors"] = a.predecessors or ""
    if new_risorse_pairs is not None:
        mpp_payload["resources"] = [rid for _, rid in new_risorse_pairs]

    MppSyncQueue.objects.create(
        operation="update_task",
        payload_json=json.dumps(mpp_payload),
    )

    result = {
        "item": a.item,
        "job_id": a.job_id,
        "activity_id": a.activity_id,
    }
    if risorse_data is not None:
        risorse_all = list(RisorsaAttivita.objects.filter(attivita_id=a.pk))
        res_fmt = format_activity_resources(risorse_all)
        result.update(
            {
                "resources": ";".join(res_fmt["flat_ids"]) or None,
                "resources_display": res_fmt["display"],
                "resources_by_tipo": res_fmt["by_tipo"],
                "resources_html": format_activity_resources_html(risorse_all),
            }
        )
    if resolve_overlaps:
        should_resolve = risorse_data is not None or any(
            key in data for key in ("actual_start", "start", "finish")
        )
        if should_resolve:
            from ui.services.overlap_reassign import auto_resolve_resource_overlaps

            result.update(auto_resolve_resource_overlaps())
    return result


def cancel_pending_task_ops(attivita_id: int) -> None:
    """Remove pending add/update queue entries for an activity."""
    for entry in MppSyncQueue.objects.filter(operation__in=["add_task", "update_task"]):
        try:
            payload = json.loads(entry.payload_json)
        except (json.JSONDecodeError, ValueError):
            continue
        if payload.get("attivita_id") == attivita_id:
            entry.delete()


def reconcile_delete(attivita: Attivita) -> None:
    """Reconcile MppSyncQueue before deleting an activity."""
    cancel_pending_task_ops(attivita.pk)
    if attivita.synced_to_mpp:
        MppSyncQueue.objects.create(
            operation="delete_task",
            payload_json=json.dumps(
                {
                    "commessa": attivita.commessa,
                    "item": attivita.item,
                    "activity": attivita.activity,
                }
            ),
        )


def delete_attivita(orm_id: int) -> None:
    """Delete an activity and reconcile the MppSyncQueue.

    Args:
        orm_id: Primary key of the activity.

    Raises:
        BridgeWriteError: When the activity does not exist.
    """
    try:
        a = Attivita.objects.get(pk=orm_id)
    except Attivita.DoesNotExist as exc:
        raise BridgeWriteError("Attività non trovata.", status_code=404) from exc

    with transaction.atomic():
        reconcile_delete(a)
        a.delete()
