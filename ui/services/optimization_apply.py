"""Apply an accepted optimization proposal to Django planning records."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from django.db import transaction

from ui.models import (
    Attivita,
    GeneratoreItem,
    MandrinoItem,
    MascheraItem,
    MppSyncQueue,
    RisorsaAttivita,
    TastatoreItem,
    TestaItem,
)
from ui.services.optimization_problem import (
    activities_for_job_ids,
    is_attivita_fixed,
    match_attivita_for_solver_activity_id,
)
from ui.services.resource_conflicts import validate_no_conflicts

_VALID_TIPOS = frozenset(dict(RisorsaAttivita.TIPO_CHOICES).keys())


class OptimizationApplyError(Exception):
    """Raised when the optimization proposal cannot be applied."""


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _lookup_tipo_by_uid(uid: str) -> str | None:
    uid = (uid or "").strip()
    if not uid:
        return None
    if TestaItem.objects.filter(uid=uid).exists():
        return "testa"
    if GeneratoreItem.objects.filter(uid=uid).exists():
        return "generatore"
    if MandrinoItem.objects.filter(uid=uid).exists():
        return "mandrino"
    if TastatoreItem.objects.filter(uid=uid).exists():
        return "tastatore"
    if MascheraItem.objects.filter(uid=uid).exists():
        return "maschera"
    return None


def solver_resource_id_to_assignment(resource_id: str) -> tuple[str, str]:
    """Map a solver resource id to ``(tipo, risorsa_id)`` for ``RisorsaAttivita``."""
    raw = (resource_id or "").strip()
    if not raw:
        raise OptimizationApplyError("Risorsa vuota nella proposta.")

    if ":" in raw:
        tipo, uid = raw.split(":", 1)
        tipo = tipo.strip().lower()
        uid = uid.strip()
        if tipo in _VALID_TIPOS and uid:
            return tipo, uid
        raise OptimizationApplyError(f"Risorsa solver non valida: {raw!r}")

    tipo = _lookup_tipo_by_uid(raw)
    if tipo:
        return tipo, raw
    raise OptimizationApplyError(f"Risorsa {raw!r} non trovata nell'inventario.")


def _resource_assignments_for_task(task: dict[str, Any]) -> list[tuple[str, str]]:
    full_ids = task.get("assigned_resource_ids") or []
    if full_ids:
        return [solver_resource_id_to_assignment(rid) for rid in full_ids]

    stripped = task.get("assigned_resources") or []
    if isinstance(stripped, str):
        stripped = [
            s.strip() for s in stripped.replace(",", ";").split(";") if s.strip()
        ]
    return [solver_resource_id_to_assignment(uid) for uid in stripped]


def apply_optimization_result(result_payload: dict[str, Any]) -> int:
    """Write proposed dates and resources from ``tasks_after`` into the database.

    Returns:
        Number of activities updated.

    Raises:
        OptimizationApplyError: On validation or persistence failure.
        ResourceConflictError: When updated assignments would conflict.
    """
    tasks_after = result_payload.get("tasks_after") or []
    if not tasks_after:
        raise OptimizationApplyError("Nessuna attività nella proposta da applicare.")

    job_ids = result_payload.get("job_ids") or []
    attivita_rows = activities_for_job_ids(job_ids) if job_ids else []
    attivita_by_pk = {a.pk: a for a in attivita_rows}
    attivita_by_id = {a.activity_id: a for a in attivita_rows}

    missing_pks = [
        int(t["orm_id"])
        for t in tasks_after
        if t.get("orm_id") is not None and int(t["orm_id"]) not in attivita_by_pk
    ]
    if missing_pks:
        for att in Attivita.objects.filter(pk__in=missing_pks).prefetch_related(
            "risorse"
        ):
            attivita_by_pk[att.pk] = att
            attivita_by_id[att.activity_id] = att
            attivita_rows.append(att)

    updates: list[tuple[Attivita, date, date, list[tuple[str, str]]]] = []
    for task in tasks_after:
        orm_id = task.get("orm_id")
        start = _parse_iso_date(task.get("start"))
        finish = _parse_iso_date(task.get("finish"))
        if orm_id is None:
            att = attivita_by_id.get(str(task.get("activity_id") or ""))
            if att is None:
                att = match_attivita_for_solver_activity_id(
                    str(task.get("activity_id") or ""),
                    attivita_rows,
                )
            if att is None:
                raise OptimizationApplyError(
                    f"Attività non trovata: {task.get('activity_id')!r}"
                )
            orm_id = att.pk
        else:
            att = attivita_by_pk.get(int(orm_id))
            if att is None:
                raise OptimizationApplyError(f"Attività non trovata (id={orm_id}).")

        if is_attivita_fixed(att):
            continue

        if start is None or finish is None:
            raise OptimizationApplyError(
                f"Date mancanti per l'attività {att.activity_id}."
            )

        pairs = _resource_assignments_for_task(task)
        updates.append((att, start, finish, pairs))

    if not updates:
        return 0

    with transaction.atomic():
        updated = 0
        for att, start, finish, pairs in updates:
            old_commessa = att.commessa
            old_item = att.item
            old_activity = att.activity

            att.data_inizio = start
            att.data_fine = finish
            att.save(
                update_fields=["data_inizio", "data_fine", "job_id", "activity_id"]
            )

            RisorsaAttivita.objects.filter(attivita_id=att.pk).delete()
            if pairs:
                RisorsaAttivita.objects.bulk_create(
                    [
                        RisorsaAttivita(attivita=att, tipo=tipo, risorsa_id=rid)
                        for tipo, rid in pairs
                    ]
                )

            MppSyncQueue.objects.create(
                operation="update_task",
                payload_json=json.dumps(
                    {
                        "attivita_id": att.pk,
                        "commessa": att.commessa,
                        "item": att.item,
                        "activity": att.activity,
                        "old_commessa": old_commessa,
                        "old_item": old_item,
                        "old_activity": old_activity,
                        "start": att.data_inizio.isoformat(),
                        "finish": att.data_fine.isoformat(),
                        "actual_start": att.actual_start.isoformat()
                        if att.actual_start
                        else None,
                        "actual_finish": att.actual_finish.isoformat()
                        if att.actual_finish
                        else None,
                        "plant": att.stabilimento or None,
                        "responsible": att.responsabile or None,
                    }
                ),
            )
            updated += 1

        # Validate against the fully-applied state: a proposal moves many
        # activities at once, so checking each one against the stale dates of
        # the others would report false conflicts. On any real conflict the
        # exception rolls back the whole transaction.
        for att, start, finish, pairs in updates:
            validate_no_conflicts(
                pairs,
                start,
                finish,
                exclude_activity_id=att.pk,
            )

    return updated
