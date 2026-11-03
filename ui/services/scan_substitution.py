"""Tablet scan of a physical resource against the planned assignment.

The tablet starts an activity by scanning its components.  Heads and
generators must match the assigned UID exactly.  Accessories (mandrini,
tastatori, maschere) are constrained by a measure, not by an identity, so a
scanned item that is dimensionally compatible may replace the planned one.

Whatever the category, and whether it matches the plan or not, an item held
by another activity in progress (started, not closed) is refused: it is in
use there until that activity records its actual finish, whatever its dates.

* **Caso 1** -- the scanned item is free in the activity window: allowed,
  with an explicit confirmation when it differs from the planned item.
* **Caso 2** -- the scanned item is held by an activity in progress, or by
  one closed within the same window: refused, nothing changes.
* **Caso 3** -- the scanned item is assigned to a planned, overlapping
  activity: allowed with the same confirmation; the robbed activity is
  re-equipped through :func:`ui.services.overlap_reassign.pick_free_spare`
  and, when nothing compatible is free, left uncovered with a note so the
  problem is visible in planning without blocking the operator.

Compatibility uses :mod:`ui.services.measures`, the same rule as the
planning picker.
"""

from __future__ import annotations

import logging

from django.db import transaction

from ui.models import Attivita, ProblemaAttivita, RisorsaAttivita
from ui.services.activity_inventory import lookup_resources
from ui.services.bridge_write import BridgeWriteError, update_attivita
from ui.services.measures import (
    has_measure_requirement,
    is_resource_compatible,
    parse_measure,
)
from ui.services.resource_types import pairs_to_risorse_dict
from ui.services.overlap_reassign import (
    _item_snapshot,
    _paren_traits,
    _resource_name,
    pick_free_spare,
)
from ui.services.problem_detection import _TIPO_LABELS, refresh_problems_for_attivita
from ui.services.resource_conflicts import (
    find_conflicts,
    format_conflict_message,
    occupation_window,
)

logger = logging.getLogger(__name__)

EXACT_TIPI: tuple[str, ...] = ("testa", "generatore")
ACCESSORY_TIPI: tuple[str, ...] = ("mandrino", "tastatore", "maschera")

ESITO_CORRISPONDENTE = "corrispondente"
ESITO_SOSTITUZIONE = "sostituzione"

_SUBSTITUTION_NOTE = "Sostituzione da scansione tablet."
_UNCOVERED_NOTE = "Nessuna alternativa nel periodo: assegnazione rimossa."


class ScanError(BridgeWriteError):
    """A scan that must be refused, carrying a machine-readable ``motivo``."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        motivo: str = "",
        conflicts: list | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, conflicts=conflicts)
        self.motivo = motivo


def scan_resource(
    pk: int,
    *,
    tipo: str,
    codice: str,
    atteso: str | None = None,
    conferma: bool = False,
) -> dict:
    """Validate a scanned resource for an activity and apply a substitution.

    Args:
        pk: Primary key of the activity being started.
        tipo: Resource category of the scanned item.
        codice: Scanned code, equal to the inventory UID.
        atteso: Planned UID the scan replaces; inferred when the activity
            holds exactly one item of *tipo*.  A check without *conferma*
            may omit it even with several items: the tablet pairs the
            substitutes with the planned pieces left over only at start.
        conferma: When ``True`` the operator confirmed the substitution and
            the planning assignments are updated.

    Returns:
        dict: Scan outcome (``esito``, ``caso``, ``richiede_conferma``,
            ``messaggio``, ``applicata`` …).

    Raises:
        BridgeWriteError: For invalid input, unknown items, out-of-range
            measures, items in use elsewhere and not-found activities.
    """
    tipo = (tipo or "").strip().lower()
    codice = (codice or "").strip()
    atteso = (atteso or "").strip() or None
    if tipo not in EXACT_TIPI + ACCESSORY_TIPI:
        raise ScanError("Tipo risorsa non valido.", status_code=400, motivo="tipo")
    if not codice:
        raise ScanError(
            "Codice scansionato mancante.", status_code=400, motivo="codice"
        )

    try:
        att = Attivita.objects.get(pk=pk)
    except Attivita.DoesNotExist as exc:
        raise ScanError("Attività non trovata.", status_code=404) from exc

    label = _TIPO_LABELS.get(tipo, tipo.capitalize())
    row = lookup_resources([(tipo, codice)]).get(codice)
    if row is None:
        raise ScanError(
            f"{label} «{codice}» non presente in inventario.",
            status_code=404,
            motivo="non_trovato",
        )

    assigned = _assigned_uids(att, tipo)
    base: dict = {"tipo": tipo, "codice": codice, "atteso": None, "avvisi": []}

    if codice not in assigned and tipo in EXACT_TIPI:
        previsto = ", ".join(f"«{u}»" for u in assigned) or "nessuna"
        raise ScanError(
            f"{label} «{codice}» non corrisponde a quella prevista ({previsto}). "
            "Per teste e generatori serve il pezzo esatto.",
            status_code=409,
            motivo="non_corrispondente",
        )

    _refuse_if_in_use(att, tipo, codice)

    if codice in assigned:
        return {
            **base,
            "esito": ESITO_CORRISPONDENTE,
            "caso": None,
            "richiede_conferma": False,
            "applicata": False,
            "messaggio": f"{label} «{codice}» corrisponde alla pianificazione.",
        }

    expected = _resolve_expected(tipo, assigned, atteso, conferma=conferma)
    base["atteso"] = expected
    _check_compatibility(att, tipo, row)

    window = occupation_window(att)
    if window is None:
        raise ScanError(
            "Per verificare la disponibilità servono data di inizio e fine.",
            status_code=400,
            motivo="date",
        )
    started, planned = _classify_conflicts(att, tipo, codice, window)
    if started:
        raise ScanError(
            format_conflict_message(
                started,
                action_hint=(
                    f"{label} «{codice}» è in uso in un'altra lavorazione: "
                    "usa un altro pezzo compatibile."
                ),
            ),
            status_code=409,
            motivo="occupata",
            conflicts=started,
        )

    caso = 3 if planned else 1
    plant = (att.stabilimento or "").strip()
    if plant and (row.get("plant") or "").strip() != plant:
        base["avvisi"].append(
            f"{label} «{codice}» risulta nello stabilimento "
            f"«{row.get('plant') or '—'}», non in «{plant}»."
        )
    messaggio = _confirm_message(
        tipo, label, codice, expected, planned, has_planned=bool(assigned)
    )

    if not conferma:
        return {
            **base,
            "esito": ESITO_SOSTITUZIONE,
            "caso": caso,
            "richiede_conferma": True,
            "applicata": False,
            "messaggio": messaggio,
            "attivita_sottratte": [_conflict_summary(c) for c in planned],
        }

    applied = _apply_substitution(att, tipo, codice, expected, planned)
    return {
        **base,
        "esito": ESITO_SOSTITUZIONE,
        "caso": caso,
        "richiede_conferma": False,
        "applicata": True,
        "messaggio": messaggio,
        **applied,
    }


def _assigned_uids(att, tipo: str) -> list[str]:
    """UIDs of *tipo* currently assigned to *att*, in insertion order."""
    return list(
        RisorsaAttivita.objects.filter(attivita_id=att.pk, tipo=tipo)
        .order_by("pk")
        .values_list("risorsa_id", flat=True)
    )


def _in_progress_holders(att, tipo: str, uid: str) -> list[dict]:
    """Other activities started and not closed that hold *uid*.

    No window applies: the piece is physically in use there until
    ``actual_finish`` is recorded, however far its dates are from *att*.

    Returns:
        list[dict]: Conflict dicts shaped like :func:`find_conflicts`, with
            the holder's occupancy dates.
    """
    rows = (
        RisorsaAttivita.objects.select_related("attivita")
        .filter(
            tipo=tipo,
            risorsa_id=uid,
            attivita__actual_start__isnull=False,
            attivita__actual_finish__isnull=True,
        )
        .exclude(attivita_id=att.pk)
        .order_by("attivita__actual_start", "pk")
    )
    holders: list[dict] = []
    for row in rows:
        start, end = occupation_window(row.attivita)
        holders.append(
            {
                "tipo": row.tipo,
                "risorsa_id": row.risorsa_id,
                "attivita_id": row.attivita_id,
                "attivita_label": str(row.attivita),
                "data_inizio": start.isoformat(),
                "data_fine": end.isoformat(),
            }
        )
    return holders


def _refuse_if_in_use(att, tipo: str, codice: str) -> None:
    """Refuse *codice* while another activity in progress holds it.

    Raises:
        ScanError: ``occupata``, whether *codice* is the planned piece or not.
    """
    holders = _in_progress_holders(att, tipo, codice)
    if not holders:
        return
    label = _TIPO_LABELS.get(tipo, tipo.capitalize())
    where, chiusa = (
        ("nell'attività", "chiusa")
        if len(holders) == 1
        else ("nelle attività", "chiuse")
    )
    names = ", ".join(h["attivita_label"] for h in holders)
    raise ScanError(
        f"{label} «{codice}» è in uso {where} {names}, non ancora {chiusa}: "
        "si potrà usare quando ne verrà registrata la fine effettiva.",
        status_code=409,
        motivo="occupata",
        conflicts=holders,
    )


def _resolve_expected(
    tipo: str, assigned: list[str], atteso: str | None, *, conferma: bool = True
) -> str | None:
    """Return the planned UID the scan replaces.

    Compatibility depends on the activity requirement, not on the piece being
    replaced, so a check without confirmation needs no pairing: the tablet
    decides which planned piece each substitute replaces only at start.

    Args:
        tipo: Resource category.
        assigned: UIDs of *tipo* currently assigned.
        atteso: UID named by the tablet, if any.
        conferma: ``False`` for a check that writes nothing.

    Returns:
        str | None: The UID to replace, or ``None`` when the activity holds no
            item of *tipo* (the scanned one is simply added) or when a check
            without confirmation names none among several.

    Raises:
        ScanError: When *atteso* is not assigned, or is required because the
            activity holds several items of *tipo* and the write is confirmed.
    """
    label = _TIPO_LABELS.get(tipo, tipo.capitalize())
    if atteso is not None:
        if atteso not in assigned:
            raise ScanError(
                f"{label} «{atteso}» non è assegnato a questa attività.",
                status_code=400,
                motivo="atteso",
            )
        return atteso
    if not assigned:
        return None
    if len(assigned) == 1:
        return assigned[0]
    if not conferma:
        return None
    raise ScanError(
        f"L'attività ha più {label.lower()} assegnati ({', '.join(assigned)}): "
        "indica quale stai sostituendo.",
        status_code=400,
        motivo="atteso",
    )


def _fmt(value) -> str:
    """Compact number text for messages (``25.5`` → ``25.5``, ``25.0`` → ``25``)."""
    parsed = parse_measure(value)
    if parsed is None:
        return "?"
    return f"{parsed:g}"


def _requirement_text(att, tipo: str) -> str:
    """Human-readable requirement of *tipo* on *att*."""
    if tipo == "maschera":
        return f"passo {_fmt(att.tube_pitch)} mm"
    return f"Ø {_fmt(att.bore_min)}–{_fmt(att.bore_max)} mm"


def _check_compatibility(att, tipo: str, row: dict) -> None:
    """Refuse the scan when the item is out of tolerance or unverifiable.

    Args:
        att: Activity carrying the requirement.
        tipo: Accessory category.
        row: Inventory row of the scanned item.

    Raises:
        ScanError: ``requisiti`` when the activity has no measure for *tipo*,
            ``misura`` when the item measure cannot be read,
            ``non_compatibile`` when it lies outside the requirement.
    """
    label = _TIPO_LABELS.get(tipo, tipo.capitalize())
    if not has_measure_requirement(
        tipo, bore_min=att.bore_min, bore_max=att.bore_max, tube_pitch=att.tube_pitch
    ):
        raise ScanError(
            f"L'attività non definisce i requisiti dimensionali per {label.lower()}: "
            "non è possibile verificare un pezzo diverso da quello previsto.",
            status_code=409,
            motivo="requisiti",
        )
    field = "pitch" if tipo == "maschera" else "diameter"
    raw = row.get(field)
    if parse_measure(raw) is None:
        raise ScanError(
            f"Misura non leggibile per {label.lower()} «{row.get('uid')}»: «{raw or ''}».",
            status_code=409,
            motivo="misura",
        )
    if not is_resource_compatible(
        tipo,
        row,
        bore_min=att.bore_min,
        bore_max=att.bore_max,
        tube_pitch=att.tube_pitch,
    ):
        measure = "passo" if tipo == "maschera" else "Ø"
        raise ScanError(
            f"{label} «{row.get('uid')}» ({measure} {_fmt(raw)} mm) fuori tolleranza: "
            f"richiesto {_requirement_text(att, tipo)}.",
            status_code=409,
            motivo="non_compatibile",
        )


def _classify_conflicts(
    att, tipo: str, uid: str, window: tuple
) -> tuple[list[dict], list[dict]]:
    """Split the bookings of *uid* overlapping *window* by execution state.

    Returns:
        tuple[list[dict], list[dict]]: ``(started, planned)`` conflict dicts
            from :func:`find_conflicts`; *started* holds activities with an
            actual date (in progress or closed), *planned* the rest.
    """
    conflicts = find_conflicts([(tipo, uid)], window[0], window[1], att.pk)
    if not conflicts:
        return [], []
    ids = {c["attivita_id"] for c in conflicts}
    state = {
        a.pk: bool(a.actual_start or a.actual_finish)
        for a in Attivita.objects.filter(pk__in=ids)
    }
    started = [c for c in conflicts if state.get(c["attivita_id"])]
    planned = [c for c in conflicts if not state.get(c["attivita_id"])]
    return started, planned


def _conflict_summary(conflict: dict) -> dict:
    """Tablet-facing summary of one robbed activity."""
    return {
        "attivita_id": conflict["attivita_id"],
        "label": conflict["attivita_label"],
        "data_inizio": conflict["data_inizio"],
        "data_fine": conflict["data_fine"],
    }


_ANY_PLANNED: dict[str, str] = {
    "mandrino": "uno dei mandrini previsti",
    "tastatore": "uno dei tastatori previsti",
    "maschera": "una delle maschere previste",
}


def _confirm_message(
    tipo: str,
    label: str,
    scanned: str,
    expected: str | None,
    planned: list[dict],
    *,
    has_planned: bool,
) -> str:
    """``Stai usando MND-117 al posto di MND-102, va bene?``

    Without *expected* but with planned pieces of *tipo* (a check before the
    tablet pairs them), the replaced piece is any of them.
    """
    if expected:
        text = f"Stai usando {scanned} al posto di {expected}, va bene?"
    elif has_planned:
        any_planned = _ANY_PLANNED.get(tipo, "un pezzo previsto")
        text = f"Stai usando {scanned} al posto di {any_planned}, va bene?"
    else:
        text = f"Stai usando {scanned}, non previsto dalla pianificazione, va bene?"
    if planned:
        names = ", ".join(sorted({c["attivita_label"] for c in planned}))
        text += (
            f" {label} {scanned} è assegnato anche a: {names}. "
            "Verrà sostituito automaticamente se esiste un pezzo compatibile."
        )
    return text


def _current_pairs(att) -> list[tuple[str, str]]:
    """``(tipo, uid)`` pairs currently assigned to *att*, in insertion order."""
    return list(
        RisorsaAttivita.objects.filter(attivita_id=att.pk)
        .order_by("pk")
        .values_list("tipo", "risorsa_id")
    )


def _current_risorse(att, tipo: str, expected: str | None, scanned: str) -> dict:
    """Full five-category dict for *att* with *expected* replaced by *scanned*."""
    pairs: list[tuple[str, str]] = []
    replaced = False
    for res_tipo, uid in _current_pairs(att):
        if res_tipo == tipo and uid == expected and not replaced:
            pairs.append((tipo, scanned))
            replaced = True
        else:
            pairs.append((res_tipo, uid))
    if not replaced:
        pairs.append((tipo, scanned))
    return pairs_to_risorse_dict(pairs)


def _strip_piece(robbed, tipo: str, uid: str) -> None:
    """Remove *uid* from *robbed* without validation."""
    RisorsaAttivita.objects.filter(attivita=robbed, tipo=tipo, risorsa_id=uid).delete()


def _apply_substitution(
    att, tipo: str, scanned: str, expected: str | None, planned: list[dict]
) -> dict:
    """Write the substitution to planning and re-equip robbed activities.

    Order matters: the scanned item is first stripped from every robbed
    activity so the current activity can book it, then each robbed activity
    is re-equipped one at a time against the updated database so two of them
    never receive the same spare.  Everything runs in one transaction.

    Args:
        att: Activity being started.
        tipo: Accessory category.
        scanned: Scanned UID.
        expected: Planned UID being replaced, or ``None``.
        planned: Conflicts on planned-only activities holding *scanned*.

    Returns:
        dict: ``riassegnazioni``, ``resources_by_tipo`` and the auto-resolve
            payload for the planning UI.
    """
    from ui.services.overlap_reassign import auto_resolve_resource_overlaps

    robbed_ids: list[int] = []
    for conflict in planned:
        if conflict["attivita_id"] not in robbed_ids:
            robbed_ids.append(conflict["attivita_id"])

    with transaction.atomic():
        robbed_atts = {a.pk: a for a in Attivita.objects.filter(pk__in=robbed_ids)}
        for robbed in robbed_atts.values():
            _strip_piece(robbed, tipo, scanned)

        result = update_attivita(
            att.pk,
            {"risorse": _current_risorse(att, tipo, expected, scanned)},
            resolve_overlaps=False,
        )
        att.refresh_from_db()
        _persist_current_note(att, tipo, scanned, expected)

        riassegnazioni = [
            _reequip_robbed(robbed_atts[robbed_pk], tipo, scanned, att)
            for robbed_pk in robbed_ids
            if robbed_pk in robbed_atts
        ]

        resolve = auto_resolve_resource_overlaps()

    return {
        "riassegnazioni": riassegnazioni,
        "resources_by_tipo": result.get("resources_by_tipo"),
        "swapped_blocked_ids": resolve.get("swapped_blocked_ids", []),
        "activity_updates": resolve.get("activity_updates", {}),
    }


def _reequip_robbed(robbed, tipo: str, uid: str, current) -> dict:
    """Give *robbed* a compatible free spare for *uid*, or leave it uncovered.

    Args:
        robbed: Planned activity that lost *uid* (already stripped).
        tipo: Accessory category.
        uid: UID taken by the scan.
        current: Activity that now holds *uid*.

    Returns:
        dict: ``{attivita_id, activity_id, label, tipo, da, a, esito}`` with
            ``esito`` ``sostituita`` or ``scoperta``.
    """
    picked = pick_free_spare(tipo, uid, robbed)
    to_uid = (picked or {}).get("to_uid")
    summary = {
        "attivita_id": robbed.pk,
        "activity_id": robbed.activity_id,
        "label": str(robbed),
        "tipo": tipo,
        "da": uid,
        "a": to_uid,
        "esito": "sostituita" if to_uid else "scoperta",
    }

    pairs = _current_pairs(robbed)
    if to_uid:
        pairs.append((tipo, to_uid))
    try:
        update_attivita(
            robbed.pk,
            {"risorse": pairs_to_risorse_dict(pairs)},
            resolve_overlaps=False,
        )
    except BridgeWriteError:
        logger.warning(
            "Scansione: impossibile aggiornare le risorse di %s (%s %s).",
            robbed.activity_id,
            tipo,
            to_uid or uid,
            exc_info=True,
        )
        if to_uid:
            RisorsaAttivita.objects.create(
                attivita=robbed, tipo=tipo, risorsa_id=to_uid
            )
        refresh_problems_for_attivita(robbed)

    _persist_robbed_note(robbed, tipo, uid, picked, current)
    return summary


def _persist_current_note(att, tipo: str, scanned: str, expected: str | None) -> None:
    """Resolved note on the started activity recording the scan choice."""
    from django.utils import timezone

    to_item = _item_snapshot(tipo, scanned)
    to_name = _resource_name(to_item, tipo=tipo, uid=scanned)
    if expected:
        from_name = _resource_name(
            _item_snapshot(tipo, expected), tipo=tipo, uid=expected
        )
        descr = (
            f"{to_name}{_paren_traits(to_item)} usato al posto di {from_name} "
            "su scansione dal tablet."
        )
    else:
        descr = f"{to_name}{_paren_traits(to_item)} aggiunto su scansione dal tablet."
    ProblemaAttivita.objects.create(
        attivita=att,
        tipo=ProblemaAttivita.TIPO_SOSTITUZIONE,
        risorsa_tipo=tipo,
        risorsa_uid=scanned,
        descrizione=descr,
        risolto=True,
        note_risoluzione=_SUBSTITUTION_NOTE,
        data_risoluzione=timezone.now(),
    )


def _persist_robbed_note(
    robbed, tipo: str, from_uid: str, picked: dict | None, current
) -> None:
    """Resolved note on the robbed activity explaining what the scan did."""
    from django.utils import timezone

    from_name = _resource_name(
        (picked or {}).get("from_item") or _item_snapshot(tipo, from_uid),
        tipo=tipo,
        uid=from_uid,
    )
    who = current.activity_id or str(current)
    if picked:
        to_uid = picked.get("to_uid") or ""
        to_name = _resource_name(picked.get("to_item"), tipo=tipo, uid=to_uid)
        descr = (
            f"{from_name} sostituita con {to_name}{_paren_traits(picked.get('to_item'))} "
            f"perché scansionata sul tablet per l'attività {who}."
        )
        note = _SUBSTITUTION_NOTE
        uid = to_uid
    else:
        descr = (
            f"{from_name} sottratta da una scansione sul tablet per l'attività {who}. "
            "Nessuna alternativa compatibile disponibile nel periodo: "
            "assegnazione rimossa, da ricoprire in pianificazione."
        )
        note = _UNCOVERED_NOTE
        uid = from_uid
    ProblemaAttivita.objects.create(
        attivita=robbed,
        tipo=ProblemaAttivita.TIPO_SOSTITUZIONE,
        risorsa_tipo=tipo,
        risorsa_uid=uid,
        riferimento_attivita=current.activity_id or "",
        descrizione=descr,
        risolto=True,
        note_risoluzione=note,
        data_risoluzione=timezone.now(),
    )

    if picked:
        from ui.services.operation_log import record_operation

        record_operation(
            source="sistema",
            azione="sistema_sostituzione",
            descrizione=descr,
            riferimento=f"{robbed.commessa}/{robbed.item}/{robbed.activity}",
        )
