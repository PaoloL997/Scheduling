"""Suggest and apply resource reassignment for delay-caused overlaps.

Occupancy used for suggestions includes the delayed finish
(:func:`~ui.services.problem_detection.effective_finish`), not only the
planned ``data_inizio``/``data_fine`` window.  The write path still uses
planned-date conflict checks; after a successful apply the pair no longer
shares the resource, so the overlap disappears.
"""

from __future__ import annotations

from datetime import date

from ui.services.problem_detection import (
    _CAT_TO_TIPO,
    _TIPO_LABELS,
    _TIPO_TO_CAT,
    _resource_pairs,
    _serialize_overlap_resource,
    effective_finish,
    find_resource_overlaps,
)


class OverlapReassignError(Exception):
    """Raised when a suggested overlap reassignment cannot be applied."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _occupation_window(attivita) -> tuple[date, date] | None:
    """Return the occupancy interval used for overlap suggestions.

    Completed activities (actual start and finish) occupy nothing.
    Delayed activities occupy ``[data_inizio, max(data_fine, effective_finish)]``.
    """
    if attivita.actual_start and attivita.actual_finish:
        return None
    if not (attivita.data_inizio and attivita.data_fine):
        return None
    end = attivita.data_fine
    fine = effective_finish(attivita)
    if fine is not None and fine > end:
        end = fine
    return attivita.data_inizio, end


def occupied_ids_with_delay(
    tipo: str,
    data_inizio: date,
    data_fine: date,
    exclude_activity_id: int | None,
    activities: list,
    pairs: dict[int, set[tuple[str, str]]],
) -> set[str]:
    """Resource IDs of *tipo* occupying ``[data_inizio, data_fine]``.

    Union of planned-date bookings and delay-extended occupation.
    """
    from ui.services.resource_conflicts import occupied_resource_ids

    occupied = occupied_resource_ids(tipo, data_inizio, data_fine, exclude_activity_id)
    for att in activities:
        if exclude_activity_id is not None and att.pk == exclude_activity_id:
            continue
        window = _occupation_window(att)
        if window is None:
            continue
        start, end = window
        if start <= data_fine and end >= data_inizio:
            occupied.update(uid for t, uid in pairs.get(att.pk, set()) if t == tipo)
    return occupied


def _inventory_model(tipo):
    from ui.models import (
        GeneratoreItem,
        MandrinoItem,
        MascheraItem,
        TastatoreItem,
        TestaItem,
    )

    return {
        "testa": TestaItem,
        "mandrino": MandrinoItem,
        "tastatore": TastatoreItem,
        "maschera": MascheraItem,
        "generatore": GeneratoreItem,
    }[tipo]


def _item_snapshot(tipo: str, uid: str, row: dict | None = None) -> dict:
    """Return display fields for one inventory item (plant, model, measures)."""
    if row is None:
        model_cls = _inventory_model(tipo)
        row = model_cls.objects.filter(pk=uid).values().first() or {}
    plant = str(row.get("plant") or "").strip() or None
    model = str(row.get("model") or "").strip() or None
    diameter = str(row.get("diameter") or "").strip() or None
    pitch = str(row.get("pitch") or "").strip() or None
    center = str(row.get("center_distance") or "").strip() or None
    if center and len(center) > 40:
        center = center[:37] + "…"
    specs_parts: list[str] = []
    spec_rows: list[dict] = []
    if model:
        specs_parts.append(f"modello {model}")
        spec_rows.append({"label": "Modello", "value": model})
    if diameter:
        specs_parts.append(f"Ø {diameter} mm")
        spec_rows.append({"label": "Diametro", "value": f"{diameter} mm"})
    if pitch:
        specs_parts.append(f"passo {pitch} mm")
        spec_rows.append({"label": "Passo", "value": f"{pitch} mm"})
    if center:
        specs_parts.append(f"interasse {center}")
        spec_rows.append({"label": "Interasse", "value": center})
    ser = _serialize_overlap_resource(tipo, uid)
    return {
        "uid": uid,
        "tipo": tipo,
        "tipo_label": _TIPO_LABELS.get(tipo, tipo.capitalize()),
        "label": ser["label"],
        "plant": plant,
        "model": model,
        "diameter": diameter,
        "pitch": pitch,
        "center_distance": center,
        "specs": ", ".join(specs_parts),
        "spec_rows": spec_rows,
    }


def _merge_resource_snapshot(res: dict) -> dict:
    """Attach plant/model/measure fields onto an overlap resource dict."""
    tipo = res.get("tipo")
    uid = res.get("uid")
    if not tipo or not uid:
        return res
    snap = _item_snapshot(tipo, uid)
    return {**res, **snap}


def _preferred_model(tipo: str, blocked, from_uid: str) -> str | None:
    model_cls = _inventory_model(tipo)
    item = model_cls.objects.filter(pk=from_uid).first()
    current_model = getattr(item, "model", None) if item is not None else None
    if tipo == "testa":
        return (current_model or blocked.modello_testa or "").strip() or None
    return (current_model or "").strip() or None


def _pick_alternative(
    *,
    tipo: str,
    from_uid: str,
    blocked,
    occupied: set[str],
    busy: set[str],
) -> dict | None:
    """Return the best free inventory item of *tipo*, or ``None``."""
    model_cls = _inventory_model(tipo)
    skip = occupied | {from_uid}
    rows = list(model_cls.objects.exclude(uid__in=skip).values())
    if not rows:
        return None
    plant = (blocked.stabilimento or "").strip()
    preferred_model = _preferred_model(tipo, blocked, from_uid)

    def rank(row: dict) -> tuple:
        same_plant = 0 if plant and (row.get("plant") or "") == plant else 1
        idle = 0 if row["uid"] not in busy else 1
        model_match = 0
        if preferred_model:
            model_match = 0 if (row.get("model") or "") == preferred_model else 1
        return (same_plant, idle, model_match, row["uid"])

    best = min(rows, key=rank)
    from_row = model_cls.objects.filter(pk=from_uid).values().first() or {}
    from_item = _item_snapshot(tipo, from_uid, from_row)
    to_item = _item_snapshot(tipo, best["uid"], best)
    to_plant = to_item["plant"]
    from_plant = from_item["plant"]
    plant = (blocked.stabilimento or "").strip()
    stab_esterno = to_plant if plant and to_plant and to_plant != plant else None
    return {
        "tipo": tipo,
        "from_uid": from_uid,
        "from_label": from_item["label"],
        "to_uid": best["uid"],
        "to_label": to_item["label"],
        "to_plant": to_plant,
        "from_plant": from_plant,
        "stab_esterno": stab_esterno,
        "from_item": from_item,
        "to_item": to_item,
    }


def _paren_traits(item: dict | None) -> str:
    """``(modello TP60, stabilimento Valbrembo)``."""
    if not item:
        return ""
    bits: list[str] = []
    if item.get("model"):
        bits.append(f"modello {item['model']}")
    elif item.get("diameter"):
        bits.append(f"Ø {item['diameter']}")
    elif item.get("pitch"):
        bits.append(f"passo {item['pitch']}")
    if item.get("plant"):
        bits.append(f"stabilimento {item['plant']}")
    return f" ({', '.join(bits)})" if bits else ""


def _resource_name(item: dict | None, *, tipo: str = "", uid: str = "") -> str:
    """``Testa OV-T1``."""
    item = item or {}
    tipo_label = item.get("tipo_label") or _TIPO_LABELS.get(
        tipo or item.get("tipo") or "", "Risorsa"
    )
    rid = uid or item.get("uid") or "?"
    return f"{tipo_label} {rid}"


def _format_swap_line(row: dict) -> str:
    """``Proposta sostituzione con risorsa Testa OV-T2 (modello …, stabilimento …)``."""
    to_item = row.get("to_item") or {}
    name = _resource_name(
        to_item, tipo=row.get("tipo") or "", uid=row.get("to_uid") or ""
    )
    return f"Proposta sostituzione con risorsa {name}{_paren_traits(to_item)}"


def _format_suggestion_summary(
    meta: dict, replacements: list[dict], unresolved: list[dict]
) -> str:
    """Plain-text proposal line(s) for tests and non-HTML clients."""
    if not replacements:
        names = ", ".join(
            u.get("from_uid") or u.get("from_label") or "?" for u in unresolved
        )
        return (
            f"Nessuna alternativa nel periodo ({names})"
            if names
            else "Nessuna alternativa nel periodo"
        )
    return " ".join(_format_swap_line(row) for row in replacements)


def build_suggestion(
    blocked,
    overlap_item: dict,
    activities: list,
    pairs: dict[int, set[tuple[str, str]]] | None = None,
) -> dict:
    """Build a replacement proposal for one blocked/late overlap pair."""
    from ui.services.activity_inventory import _open_assignment_ids

    if pairs is None:
        pairs = {a.pk: _resource_pairs(a) for a in activities}
    start = blocked.data_inizio
    end = blocked.data_fine
    replacements: list[dict] = []
    unresolved: list[dict] = []
    late = next(
        (a for a in activities if a.pk == overlap_item.get("peer_orm_id")),
        None,
    )
    meta = {
        "blocked_orm_id": blocked.pk,
        "blocked_label": blocked.activity or str(blocked),
        "blocked_activity_id": blocked.activity_id,
        "blocked_commessa": blocked.commessa,
        "blocked_plant": (blocked.stabilimento or "").strip() or None,
        "blocked_started": bool(blocked.actual_start),
        "late_label": overlap_item.get("peer_label")
        or (late.activity if late else None),
        "late_activity_id": overlap_item.get("peer_activity_id")
        or (late.activity_id if late else None),
        "late_plant": ((late.stabilimento or "").strip() or None) if late else None,
    }
    if not (start and end):
        unresolved = [
            {
                "tipo": r.get("tipo"),
                "from_uid": r.get("uid"),
                "from_label": r.get("label"),
            }
            for r in (overlap_item.get("resources") or [])
        ]
        return {
            **meta,
            "replacements": [],
            "unresolved": unresolved,
            "summary": _format_suggestion_summary(meta, [], unresolved),
        }

    for res in overlap_item.get("resources") or []:
        tipo = res.get("tipo")
        uid = res.get("uid")
        if not tipo or not uid:
            continue
        occupied = occupied_ids_with_delay(
            tipo, start, end, blocked.pk, activities, pairs
        )
        busy = _open_assignment_ids(tipo, blocked.pk)
        picked = _pick_alternative(
            tipo=tipo,
            from_uid=uid,
            blocked=blocked,
            occupied=occupied,
            busy=busy,
        )
        if picked:
            replacements.append(picked)
        else:
            snap = _item_snapshot(tipo, uid)
            unresolved.append(
                {
                    "tipo": tipo,
                    "from_uid": uid,
                    "from_label": snap["label"],
                    "from_item": snap,
                }
            )

    return {
        **meta,
        "replacements": replacements,
        "unresolved": unresolved,
        "summary": _format_suggestion_summary(meta, replacements, unresolved),
    }


def enrich_overlaps_with_suggestions(
    overlaps_by_pk: dict[int, list[dict]],
    activities: list | None = None,
) -> dict[int, list[dict]]:
    """Attach ``suggestion`` to each overlap item (computed on the blocked side)."""
    from ui.models import Attivita

    if not overlaps_by_pk:
        return overlaps_by_pk
    if activities is None:
        activities = list(Attivita.objects.prefetch_related("risorse"))
    pairs = {a.pk: _resource_pairs(a) for a in activities}
    by_id = {a.pk: a for a in activities}
    for items in overlaps_by_pk.values():
        for item in items:
            item["resources"] = [
                _merge_resource_snapshot(r) for r in (item.get("resources") or [])
            ]
    cache: dict[tuple[int, int], dict] = {}
    for pk, items in overlaps_by_pk.items():
        for item in items:
            if item.get("role") != "blocked":
                continue
            blocked = by_id.get(pk)
            if blocked is None:
                continue
            key = (pk, item.get("peer_orm_id"))
            if key not in cache:
                cache[key] = build_suggestion(blocked, item, activities, pairs)
            item["suggestion"] = cache[key]
    for pk, items in overlaps_by_pk.items():
        for item in items:
            if item.get("role") != "late":
                continue
            key = (item.get("peer_orm_id"), pk)
            if key in cache:
                item["suggestion"] = cache[key]
    return overlaps_by_pk


def _pairs_to_risorse(pairs: list[tuple[str, str]]) -> dict[str, list[str]]:
    data = {cat: [] for cat in _CAT_TO_TIPO}
    for tipo, uid in pairs:
        cat = _TIPO_TO_CAT.get(tipo)
        if cat:
            data[cat].append(uid)
    return data


def apply_overlap_reassignment(blocked_pk: int) -> dict:
    """Recompute the suggestion for *blocked_pk* and apply it.

    Returns:
        dict: ``update_attivita`` payload plus overlap refresh fields.

    Raises:
        OverlapReassignError: When there is nothing to apply.
    """
    from ui.models import Attivita, RisorsaAttivita
    from ui.services.bridge_write import BridgeWriteError, update_attivita

    try:
        Attivita.objects.get(pk=blocked_pk)
    except Attivita.DoesNotExist as exc:
        raise OverlapReassignError("Attività non trovata.", status_code=404) from exc

    activities = list(Attivita.objects.prefetch_related("risorse"))
    overlaps = find_resource_overlaps(activities)
    enrich_overlaps_with_suggestions(overlaps, activities)
    items = [
        item for item in overlaps.get(blocked_pk, []) if item.get("role") == "blocked"
    ]
    if not items:
        raise OverlapReassignError(
            "Nessuna sovrapposizione da risolvere su questa attività."
        )

    replacements: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        for row in (item.get("suggestion") or {}).get("replacements") or []:
            key = (row["tipo"], row["from_uid"])
            if key in seen:
                continue
            seen.add(key)
            replacements.append(row)
    if not replacements:
        raise OverlapReassignError(
            "Nessuna risorsa alternativa disponibile nel periodo."
        )

    current = list(
        RisorsaAttivita.objects.filter(attivita_id=blocked_pk).values_list(
            "tipo", "risorsa_id"
        )
    )
    replace_map = {(r["tipo"], r["from_uid"]): r["to_uid"] for r in replacements}
    new_pairs: list[tuple[str, str]] = []
    replaced = 0
    for tipo, uid in current:
        to_uid = replace_map.get((tipo, uid))
        if to_uid:
            new_pairs.append((tipo, to_uid))
            replaced += 1
        else:
            new_pairs.append((tipo, uid))
    if replaced == 0:
        raise OverlapReassignError(
            "Le risorse in conflitto non coincidono più con la proposta."
        )

    try:
        result = update_attivita(blocked_pk, {"risorse": _pairs_to_risorse(new_pairs)})
    except BridgeWriteError as exc:
        raise OverlapReassignError(str(exc), status_code=exc.status_code) from exc

    result["blocked_orm_id"] = blocked_pk
    result["replacements"] = replacements
    return result
