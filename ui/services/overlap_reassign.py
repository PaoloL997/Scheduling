"""Auto-resolve delay-caused resource overlaps by swapping the blocked activity.

When a late start makes two activities share a resource, the blocked activity
is reassigned to a compatible free item if one exists.  Successful swaps are
recorded as resolved notes; remaining overlaps become open problems with no
resolution action yet.

Occupancy comes from the single shared definition in
:func:`ui.services.resource_conflicts.occupation_window`, so a delayed
activity holds its resources past the planned end and a completed one
releases them at ``actual_finish``.
"""

from __future__ import annotations


from ui.services.measures import parse_measure
from ui.services.resource_conflicts import occupied_resource_ids
from ui.services.resource_types import (
    TIPO_LABELS as _TIPO_LABELS,
    TIPO_TO_CATEGORIA as _TIPO_TO_CAT,
    model_for_tipo,
    pairs_to_risorse_dict,
)
from ui.services.problem_detection import (
    _serialize_overlap_resource,
    find_resource_overlaps,
    overdue_not_started,
    sync_overdue_problems,
)

#: Problem types whose open rows carry a single-activity reschedule proposal.
_RESCHEDULABLE_TIPI = ("sovrapposizione", "ritardo")

#: Proposal summary for an overlap whose movable side is already running.
STARTED_NO_SWAP_SUMMARY = (
    "Attività già avviata: le risorse in uso non vengono sostituite automaticamente."
)


class OverlapReassignError(Exception):
    """Raised when a suggested overlap reassignment cannot be applied."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _item_snapshot(tipo: str, uid: str, row: dict | None = None) -> dict:
    """Return display fields for one inventory item (plant, model, measures)."""
    if row is None:
        model_cls = model_for_tipo(tipo)
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


def _fmt_iso_day(value) -> str:
    """Format ``YYYY-MM-DD`` or a date as ``DD/MM/YYYY``."""
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return f"{text[8:10]}/{text[5:7]}/{text[0:4]}"
    return text or "—"


def _peer_label(overlap_item: dict) -> str:
    parts = [
        overlap_item.get("peer_commessa"),
        overlap_item.get("peer_item"),
        overlap_item.get("peer_activity") or overlap_item.get("peer_label"),
    ]
    joined = " - ".join(
        str(p).strip() for p in parts if p is not None and str(p).strip()
    )
    return joined or overlap_item.get("peer_activity_id") or "un'altra attività"


def _preferred_model(tipo: str, blocked, from_uid: str) -> str | None:
    model_cls = model_for_tipo(tipo)
    item = model_cls.objects.filter(pk=from_uid).first()
    current_model = getattr(item, "model", None) if item is not None else None
    if tipo == "testa":
        return (current_model or blocked.modello_testa or "").strip() or None
    return (current_model or "").strip() or None


def _blocked_availability(blocked) -> dict | None:
    """Wizard-compatible inventory lists for the window the activity holds."""
    from ui.services.activity_inventory import get_available_resources
    from ui.services.resource_conflicts import candidate_window

    if not (blocked.data_inizio and blocked.data_fine):
        return None
    start, end = candidate_window(blocked.data_inizio, blocked.data_fine, blocked)
    return get_available_resources(
        stabilimento=blocked.stabilimento or "",
        data_inizio=start,
        data_fine=end,
        bore_min=parse_measure(blocked.bore_min),
        bore_max=parse_measure(blocked.bore_max),
        bore_mid=parse_measure(blocked.bore_mid),
        tube_pitch=parse_measure(blocked.tube_pitch),
        modello_testa=(blocked.modello_testa or "").strip() or None,
        exclude_activity_id=blocked.pk,
        strict_requirements=True,
    )


def _pick_alternative(
    *,
    tipo: str,
    from_uid: str,
    blocked,
    occupied: set[str],
    busy: set[str],
    candidates: list[dict] | None = None,
) -> dict | None:
    """Return the best compatible free inventory item of *tipo*, or ``None``."""
    model_cls = model_for_tipo(tipo)
    skip = occupied | {from_uid}
    if candidates is None:
        qs = model_cls.objects.exclude(uid__in=skip)
        if tipo in ("testa", "generatore"):
            qs = qs.filter(in_manutenzione=False)
        rows = list(qs.values())
    else:
        rows = [
            r
            for r in candidates
            if r.get("uid") not in skip and not r.get("in_manutenzione")
        ]
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


def pick_free_spare(tipo: str, from_uid: str, att) -> dict | None:
    """Return the best compatible free replacement for *from_uid* on *att*.

    Composes the planning availability lists (same requirements, same plant
    preference, same occupancy) with :func:`_pick_alternative`, so every
    automatic substitution -- delay overlap, maintenance, tablet scan -- uses
    one rule.

    Args:
        tipo: Resource category of the item being replaced.
        from_uid: UID that must be replaced (never returned as a pick).
        att: Activity that needs the replacement; requires planned dates.

    Returns:
        dict | None: Pick dict (``to_uid``, ``to_item`` …) or ``None`` when
            the activity has no planned window or nothing compatible is free.
    """
    from ui.services.activity_inventory import _open_assignment_ids
    from ui.services.resource_conflicts import candidate_window

    if not (att.data_inizio and att.data_fine):
        return None

    start, end = candidate_window(att.data_inizio, att.data_fine, att)
    occupied = occupied_resource_ids(tipo, start, end, att.pk)
    busy = _open_assignment_ids(tipo, att.pk)
    availability = _blocked_availability(att)
    cat = _TIPO_TO_CAT.get(tipo)
    candidates = (availability or {}).get(cat) if cat else None
    if candidates is None:
        return None
    return _pick_alternative(
        tipo=tipo,
        from_uid=from_uid,
        blocked=att,
        occupied=occupied,
        busy=busy,
        candidates=candidates,
    )


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


def build_suggestion(blocked, overlap_item: dict, activities: list) -> dict:
    """Build a replacement proposal for one blocked/late overlap pair."""
    from ui.services.activity_inventory import _open_assignment_ids

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
    if not (start and end) or blocked.actual_start:
        # No planned window, or an activity already under way: the physical
        # resources are in use, nothing gets swapped under the operator.
        unresolved = [
            {
                "tipo": r.get("tipo"),
                "from_uid": r.get("uid"),
                "from_label": r.get("label"),
            }
            for r in (overlap_item.get("resources") or [])
        ]
        summary = (
            STARTED_NO_SWAP_SUMMARY
            if blocked.actual_start
            else _format_suggestion_summary(meta, [], unresolved)
        )
        return {
            **meta,
            "replacements": [],
            "unresolved": unresolved,
            "summary": summary,
        }

    availability = _blocked_availability(blocked)
    for res in overlap_item.get("resources") or []:
        tipo = res.get("tipo")
        uid = res.get("uid")
        if not tipo or not uid:
            continue
        occupied = occupied_resource_ids(tipo, start, end, blocked.pk)
        busy = _open_assignment_ids(tipo, blocked.pk)
        cat = _TIPO_TO_CAT.get(tipo)
        candidates = (availability or {}).get(cat) if cat else None
        picked = _pick_alternative(
            tipo=tipo,
            from_uid=uid,
            blocked=blocked,
            occupied=occupied,
            busy=busy,
            candidates=candidates,
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
                cache[key] = build_suggestion(blocked, item, activities)
            item["suggestion"] = cache[key]
    for pk, items in overlaps_by_pk.items():
        for item in items:
            if item.get("role") != "late":
                continue
            key = (item.get("peer_orm_id"), pk)
            if key in cache:
                item["suggestion"] = cache[key]
    return overlaps_by_pk


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
        target = Attivita.objects.get(pk=blocked_pk)
    except Attivita.DoesNotExist as exc:
        raise OverlapReassignError("Attività non trovata.", status_code=404) from exc
    if target.actual_start:
        raise OverlapReassignError(STARTED_NO_SWAP_SUMMARY, status_code=409)

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
        result = update_attivita(
            blocked_pk,
            {"risorse": pairs_to_risorse_dict(new_pairs)},
            resolve_overlaps=False,
        )
    except BridgeWriteError as exc:
        raise OverlapReassignError(str(exc), status_code=exc.status_code) from exc

    blocked = Attivita.objects.get(pk=blocked_pk)
    _persist_swap_notes(blocked, replacements, items[0])

    result["blocked_orm_id"] = blocked_pk
    result["replacements"] = replacements
    return result


def _open_overlap_description(overlap_item: dict, res: dict, *, role: str) -> str:
    """Italian description for an overlap that cannot be auto-swapped."""
    snap = (
        res
        if res.get("tipo_label")
        else _item_snapshot(res.get("tipo") or "", res.get("uid") or "")
    )
    name = _resource_name(snap, tipo=res.get("tipo") or "", uid=res.get("uid") or "")
    traits = _paren_traits(snap)
    peer = _peer_label(overlap_item)
    until = _fmt_iso_day(overlap_item.get("overlap_end"))
    if role == "blocked":
        return (
            f"La risorsa {name}{traits} è in uso su {peer} fino al {until}. "
            "Nessuna alternativa disponibile nel periodo."
        )
    return (
        f"La risorsa {name}{traits} è condivisa con {peer} fino al {until}. "
        "Nessuna alternativa disponibile nel periodo."
    )


def _persist_swap_notes(blocked, replacements: list[dict], overlap_item: dict) -> None:
    """Store a resolved note on the blocked activity for each successful swap."""
    from django.utils import timezone
    from ui.models import ProblemaAttivita

    late_id = overlap_item.get("peer_activity_id") or ""
    late_label = _peer_label(overlap_item)
    until = _fmt_iso_day(overlap_item.get("overlap_end"))
    now = timezone.now()
    for row in replacements:
        from_name = _resource_name(
            row.get("from_item"),
            tipo=row.get("tipo") or "",
            uid=row.get("from_uid") or "",
        )
        to_name = _resource_name(
            row.get("to_item"), tipo=row.get("tipo") or "", uid=row.get("to_uid") or ""
        )
        descr = (
            f"{from_name} sostituita con {to_name}"
            f"{_paren_traits(row.get('to_item'))} perché in uso su {late_label} "
            f"fino al {until}."
        )
        already = ProblemaAttivita.objects.filter(
            attivita=blocked,
            tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
            risolto=True,
            risorsa_tipo=row["tipo"],
            risorsa_uid=row["to_uid"],
            riferimento_attivita=late_id,
            descrizione=descr,
        ).exists()
        if already:
            continue
        ProblemaAttivita.objects.create(
            attivita=blocked,
            tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
            risorsa_tipo=row["tipo"],
            risorsa_uid=row["to_uid"],
            riferimento_attivita=late_id,
            descrizione=descr,
            risolto=True,
            note_risoluzione="Sostituzione automatica per sovrapposizione risorse.",
            data_risoluzione=now,
        )

        from ui.services.operation_log import record_operation

        record_operation(
            source="sistema",
            azione="sistema_sostituzione",
            descrizione=descr,
            riferimento=f"{blocked.commessa}/{blocked.item}/{blocked.activity}",
        )


def _overlap_problem_sig(attivita_id: int, tipo: str, uid: str, peer_id: str) -> tuple:
    return (attivita_id, tipo, uid, peer_id or "")


def _sync_open_overlap_problems(
    remaining: dict[int, list[dict]],
    activities: list,
) -> None:
    """Create or close open overlap problems to match remaining conflicts."""
    from django.utils import timezone
    from ui.models import ProblemaAttivita

    by_id = {a.pk: a for a in activities}
    desired: dict[tuple, dict] = {}
    for pk, items in remaining.items():
        att = by_id.get(pk)
        if att is None:
            continue
        for item in items:
            # The note belongs to the side that can still be moved; the peer
            # only reserves the resource and is not itself flagged.
            role = item.get("role") or "blocked"
            if role != "blocked":
                continue
            peer_id = item.get("peer_activity_id") or ""
            for res in item.get("resources") or []:
                tipo = res.get("tipo") or ""
                uid = res.get("uid") or ""
                if not tipo or not uid:
                    continue
                sig = _overlap_problem_sig(pk, tipo, uid, peer_id)
                desired[sig] = {
                    "attivita": att,
                    "tipo": tipo,
                    "uid": uid,
                    "peer_id": peer_id,
                    "descrizione": _open_overlap_description(item, res, role=role),
                    "bloccata_fino": item.get("overlap_end"),
                }

    open_qs = ProblemaAttivita.objects.filter(
        tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
        risolto=False,
    )
    now = timezone.now()
    seen: set[tuple] = set()
    for problema in open_qs:
        sig = _overlap_problem_sig(
            problema.attivita_id,
            problema.risorsa_tipo,
            problema.risorsa_uid,
            problema.riferimento_attivita,
        )
        wanted = desired.get(sig)
        if wanted is None:
            problema.risolto = True
            problema.note_risoluzione = (
                "Condizione non piu verificata dopo aggiornamento attivita."
            )
            problema.data_risoluzione = now
            problema.save(
                update_fields=["risolto", "note_risoluzione", "data_risoluzione"]
            )

            from ui.services.operation_log import record_operation

            resolved_att = problema.attivita
            record_operation(
                source="sistema",
                azione="sistema_problema_risolto",
                descrizione=(
                    f"Sovrapposizione risolta automaticamente: {problema.descrizione}"
                ),
                riferimento=(
                    f"{resolved_att.commessa}/{resolved_att.item}/"
                    f"{resolved_att.activity}"
                ),
            )
            continue
        seen.add(sig)
        updates: list[str] = []
        if problema.descrizione != wanted["descrizione"]:
            problema.descrizione = wanted["descrizione"]
            updates.append("descrizione")
        until = wanted["bloccata_fino"]
        if until and str(problema.bloccata_fino or "") != str(until):
            from datetime import date as date_cls

            try:
                problema.bloccata_fino = date_cls.fromisoformat(str(until)[:10])
            except ValueError:
                problema.bloccata_fino = None
            updates.append("bloccata_fino")
            # The overlap window moved: any stored proposal is stale, force a
            # fresh one on the next reschedule-proposal sync.
            if problema.proposta_json is not None:
                problema.proposta_json = None
                updates.append("proposta_json")
        if updates:
            problema.save(update_fields=updates)

    to_create = []
    for sig, wanted in desired.items():
        if sig in seen:
            continue
        until = wanted["bloccata_fino"]
        bloccata = None
        if until:
            from datetime import date as date_cls

            try:
                bloccata = date_cls.fromisoformat(str(until)[:10])
            except ValueError:
                bloccata = None
        to_create.append(
            ProblemaAttivita(
                attivita=wanted["attivita"],
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
                risorsa_tipo=wanted["tipo"],
                risorsa_uid=wanted["uid"],
                riferimento_attivita=wanted["peer_id"],
                descrizione=wanted["descrizione"],
                bloccata_fino=bloccata,
                risolto=False,
            )
        )
    if to_create:
        ProblemaAttivita.objects.bulk_create(to_create)


def _activity_update_payload(attivita) -> dict:
    """Row fields the pianificazione UI needs after an auto-resolve."""
    from ui.models import ProblemaAttivita, RisorsaAttivita
    from ui.services.resource_display import (
        format_activity_resources,
        format_activity_resources_html,
    )

    from django.utils.timezone import localdate
    from ui.services.planning_read import (
        activity_state_key,
        compute_activity_status,
        compute_delay_days,
    )

    risorse_all = list(RisorsaAttivita.objects.filter(attivita=attivita))
    res_fmt = format_activity_resources(risorse_all)
    problemi = list(ProblemaAttivita.objects.filter(attivita=attivita))
    today = localdate()
    dates = (
        attivita.data_inizio,
        attivita.data_fine,
        attivita.actual_start,
        attivita.actual_finish,
    )
    return {
        "resources": ";".join(res_fmt["flat_ids"]) or None,
        "resources_by_tipo": res_fmt["by_tipo"],
        "resources_html": format_activity_resources_html(risorse_all),
        "warning_count": sum(1 for p in problemi if not p.risolto),
        "note_count": sum(1 for p in problemi if p.risolto),
        "state_key": activity_state_key(compute_activity_status(*dates, today)),
        "delay_days": compute_delay_days(*dates, today),
    }


def auto_resolve_resource_overlaps() -> dict:
    """Swap compatible resources on blocked activities; persist notes/problems.

    Returns:
        dict: ``swapped_blocked_ids`` and ``activity_updates`` keyed by pk.
    """
    from ui.models import Attivita

    activities = list(Attivita.objects.prefetch_related("risorse"))
    overlaps = find_resource_overlaps(activities)
    if not overlaps:
        _sync_open_overlap_problems({}, activities)
        overdue_pks = {a.pk for a in activities if overdue_not_started(a)}
        sync_overdue_problems(activities)
        updates = {
            str(att.pk): _activity_update_payload(att)
            for att in Attivita.objects.filter(pk__in=overdue_pks)
        }
        return {"swapped_blocked_ids": [], "activity_updates": updates}

    enrich_overlaps_with_suggestions(overlaps, activities)
    swapped_ids: list[int] = []
    affected: set[int] = set()
    by_id = {a.pk: a for a in activities}

    for pk, items in list(overlaps.items()):
        blocked_items = [i for i in items if i.get("role") == "blocked"]
        if not blocked_items:
            continue
        blocked_att = by_id.get(pk)
        if blocked_att is None or blocked_att.actual_start:
            # Never swap the resources of a running activity.
            continue
        replacements: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for item in blocked_items:
            for row in (item.get("suggestion") or {}).get("replacements") or []:
                key = (row["tipo"], row["from_uid"])
                if key in seen:
                    continue
                seen.add(key)
                replacements.append(row)
        if not replacements:
            continue
        try:
            apply_overlap_reassignment(pk)
        except OverlapReassignError:
            continue
        swapped_ids.append(pk)
        affected.add(pk)
        for item in blocked_items:
            peer = item.get("peer_orm_id")
            if peer:
                affected.add(int(peer))

    activities = list(Attivita.objects.prefetch_related("risorse"))
    remaining = find_resource_overlaps(activities)
    _sync_open_overlap_problems(remaining, activities)
    overdue_pks = {a.pk for a in activities if overdue_not_started(a)}
    sync_overdue_problems(activities)
    for pk in remaining:
        affected.add(pk)
    affected |= overdue_pks

    updates = {}
    if affected:
        for att in Attivita.objects.filter(pk__in=affected):
            updates[str(att.pk)] = _activity_update_payload(att)

    return {"swapped_blocked_ids": swapped_ids, "activity_updates": updates}


# -- targeted single-activity reschedule proposals ----------------------------


def _merge_unmodeled_resources(att, task: dict) -> dict:
    """Keep ORM resources the solver did not model on ``task``.

    The CP model only assigns resource types implied by the activity
    requirements. A reschedule proposal must not wipe mandrini/tastatori/…
    that were already assigned but are outside that subset.
    """
    current = [(r.tipo, r.risorsa_id) for r in att.risorse.all()]
    solver_ids = list(task.get("assigned_resource_ids") or [])
    solver_uids = list(task.get("assigned_resources") or [])
    if not solver_uids:
        task["assigned_resource_ids"] = [f"{tipo}:{uid}" for tipo, uid in current]
        task["assigned_resources"] = [uid for _, uid in current]
        return task
    solver_tipos: set[str] = set()
    for rid in solver_ids:
        if ":" in rid:
            solver_tipos.add(rid.split(":", 1)[0])
    kept = [(tipo, uid) for tipo, uid in current if tipo not in solver_tipos]
    if kept:
        task["assigned_resource_ids"] = solver_ids + [
            f"{tipo}:{uid}" for tipo, uid in kept
        ]
        task["assigned_resources"] = solver_uids + [uid for _, uid in kept]
    return task


def _resource_labels(assigned_resource_ids: list[str]) -> list[str]:
    """Turn solver ``tipo:uid`` ids into display labels like ``Mandrino M1``."""
    labels: list[str] = []
    for rid in assigned_resource_ids or []:
        if ":" in rid:
            tipo, uid = rid.split(":", 1)
        else:
            tipo, uid = "", rid
        label = _TIPO_LABELS.get(tipo, tipo.capitalize() or "Risorsa")
        labels.append(f"{label} {uid}".strip())
    return labels


def propose_reschedule(blocked_pk: int) -> dict | None:
    """Run the optimizer on a single blocked activity and build a proposal.

    The blocked activity plus its precedence-connected chain are the only
    schedulable activities; everything else is reserved. Returns a proposal
    dict (with a serialisable ``tasks_after`` payload for applying) or ``None``
    when infeasible or when nothing would change.
    """
    from ui.models import Attivita, Job as JobModel
    from ui.services.optimization_problem import (
        OptimizationProblemError,
        build_reschedule_problem,
        schedule_result_to_gantt_tasks,
    )
    from ui.services.runtime_config import load_runtime_config

    try:
        blocked = Attivita.objects.get(pk=blocked_pk)
    except Attivita.DoesNotExist:
        return None

    try:
        problem = build_reschedule_problem(blocked_pk)
    except OptimizationProblemError:
        return None

    from src.optimization.feasibility import check_feasibility
    from src.optimization.solver import schedule

    if not check_feasibility(problem).feasible:
        return None
    result, _diagnoses = schedule(
        problem, time_limit=load_runtime_config().solver_time_limit
    )
    if result is None:
        return None

    activities = list(Attivita.objects.prefetch_related("risorse"))
    by_id = {a.activity_id: a for a in activities}
    tasks = schedule_result_to_gantt_tasks(result, by_id, {}, attivita=activities)

    changed: list[dict] = []
    blocked_task: dict | None = None
    for task in tasks:
        att = by_id.get(task.get("activity_id"))
        if att is None:
            continue
        _merge_unmodeled_resources(att, task)
        new_start = task.get("start")
        new_finish = task.get("finish")
        new_res = sorted(task.get("assigned_resources") or [])
        cur_start = att.data_inizio.isoformat() if att.data_inizio else None
        cur_finish = att.data_fine.isoformat() if att.data_fine else None
        cur_res = sorted(r.risorsa_id for r in att.risorse.all())
        if att.pk == blocked_pk:
            blocked_task = task
        if (new_start, new_finish, new_res) != (cur_start, cur_finish, cur_res):
            changed.append(task)

    if not changed or blocked_task is None:
        return None
    # The optimizer only assigns resources it models (from the activity's
    # requirements). If it assigned none, the overlapping resource is not
    # modelled here and moving the date would wrongly drop the assignment.
    if not blocked_task.get("assigned_resources"):
        return None

    due_iso: str | None = None
    meets_deadline: bool | None = None
    job_row = JobModel.objects.filter(commessa=blocked.commessa).first()
    if job_row is not None and job_row.delivery_date:
        due_iso = (
            job_row.delivery_date.isoformat()
            if hasattr(job_row.delivery_date, "isoformat")
            else str(job_row.delivery_date)[:10]
        )
        finish = blocked_task.get("finish")
        if finish and due_iso:
            meets_deadline = str(finish)[:10] <= due_iso

    moved_by_id = {t.get("activity_id"): t for t in changed}
    gantt_acts: list[dict] = []
    for att in activities:
        if att.commessa != blocked.commessa:
            continue
        moved = moved_by_id.get(att.activity_id)
        label = f"{att.item} · {att.activity}".strip()
        gantt_acts.append(
            {
                "activity_id": att.activity_id,
                "label": label or att.activity_id,
                "item": str(att.item) if att.item is not None else "",
                "activity": att.activity or "",
                "commessa": att.commessa,
                "job_id": att.job_id,
                "start": att.data_inizio.isoformat() if att.data_inizio else None,
                "finish": att.data_fine.isoformat() if att.data_fine else None,
                "proposed_start": moved.get("start") if moved else None,
                "proposed_finish": moved.get("finish") if moved else None,
                "assigned_resources": [r.risorsa_id for r in att.risorse.all()],
                "proposed_resources": (
                    list(moved.get("assigned_resources") or []) if moved else None
                ),
                "is_blocked": att.pk == blocked_pk,
                "started": att.actual_start is not None,
            }
        )
    # Same order as the main Gantt, which reads the activities through
    # _fetch_tasks (job_id, data_inizio, activity_id). A missing planned
    # start sorts first there too.
    gantt_acts.sort(
        key=lambda a: (a["job_id"] or "", a["start"] or "", a["activity_id"])
    )

    return {
        "status": "proposed",
        "blocked_pk": blocked_pk,
        "tasks_after": changed,
        "blocked": {
            "orm_id": blocked_pk,
            "activity_id": blocked_task.get("activity_id"),
            "start": blocked_task.get("start"),
            "finish": blocked_task.get("finish"),
            "resources": sorted(blocked_task.get("assigned_resources") or []),
            "resource_labels": _resource_labels(
                blocked_task.get("assigned_resource_ids") or []
            ),
        },
        "moved_count": len(changed),
        "due_date": due_iso,
        "meets_deadline": meets_deadline,
        "commessa": blocked.commessa,
        "job_id": blocked.job_id,
        "gantt": {
            "due_date": due_iso,
            "job_id": blocked.job_id,
            "activities": gantt_acts,
        },
    }


def compute_reschedule_proposal(blocked_pk: int, *, store: bool = True) -> dict:
    """Solve a fresh reschedule proposal for one activity.

    The notes modal asks for this every time it opens. A proposal is never
    reused: editing any other activity can invalidate it, and a stale one is
    worse than none. A proposal the planner rejected is returned untouched,
    so dismissing it stays a decision and not a one-render pause.

    Args:
        blocked_pk: Primary key of the activity to re-plan.
        store: Persist the payload on the activity's open reschedulable
            rows, which is what :func:`apply_reschedule` later applies.
            Read-only callers pass ``False``.

    Returns:
        dict: The proposal payload, or a ``status``-only marker
        (``no_problem``, ``no_proposal``, ``rejected``).
    """
    from ui.models import ProblemaAttivita

    rows = list(
        ProblemaAttivita.objects.filter(
            attivita_id=blocked_pk,
            tipo__in=_RESCHEDULABLE_TIPI,
            risolto=False,
        )
    )
    if not rows:
        return {"status": "no_problem"}
    for row in rows:
        payload = row.proposta_json
        if isinstance(payload, dict) and payload.get("status") == "rejected":
            return payload

    payload = propose_reschedule(blocked_pk) or {"status": "no_proposal"}
    if store:
        for row in rows:
            row.proposta_json = payload
            row.save(update_fields=["proposta_json"])
    return payload


def _stored_proposal(blocked_pk: int) -> dict | None:
    """Return the current 'proposed' payload for an activity, if any."""
    from ui.models import ProblemaAttivita

    for row in ProblemaAttivita.objects.filter(
        attivita_id=blocked_pk,
        tipo__in=_RESCHEDULABLE_TIPI,
        risolto=False,
    ):
        payload = row.proposta_json
        if isinstance(payload, dict) and payload.get("status") == "proposed":
            return payload
    return None


def apply_reschedule(blocked_pk: int) -> dict:
    """Apply the stored reschedule proposal for a blocked activity.

    Raises:
        OverlapReassignError: When there is no applicable proposal.
        ResourceConflictError: When the applied schedule conflicts (rolled back).
    """
    from ui.services.optimization_apply import (
        OptimizationApplyError,
        apply_optimization_result,
    )

    proposal = _stored_proposal(blocked_pk)
    if not proposal:
        raise OverlapReassignError("Nessuna proposta di ripianificazione disponibile.")
    tasks_after = proposal.get("tasks_after") or []
    if not tasks_after:
        raise OverlapReassignError("La proposta non contiene modifiche da applicare.")

    try:
        updated = apply_optimization_result(
            {"tasks_after": tasks_after, "job_ids": []}, reconcile_after=False
        )
    except OptimizationApplyError as exc:
        raise OverlapReassignError(str(exc)) from exc

    # One reconciliation pass: closes the overlap, refreshes the notes of the
    # moved activities and returns the row updates the page applies.
    from ui.services.reconcile import reconcile

    result = reconcile(
        [int(t["orm_id"]) for t in tasks_after if t.get("orm_id") is not None]
    )
    result["updated"] = updated
    result["blocked_orm_id"] = blocked_pk
    return result


def reject_reschedule(blocked_pk: int) -> dict:
    """Mark the stored proposal as rejected so it is not shown or recomputed."""
    from ui.models import ProblemaAttivita

    count = 0
    for row in ProblemaAttivita.objects.filter(
        attivita_id=blocked_pk,
        tipo__in=_RESCHEDULABLE_TIPI,
        risolto=False,
    ):
        if (
            isinstance(row.proposta_json, dict)
            and row.proposta_json.get("status") == "proposed"
        ):
            row.proposta_json = {"status": "rejected"}
            row.save(update_fields=["proposta_json"])
            count += 1
    return {"blocked_orm_id": blocked_pk, "rejected": count}
