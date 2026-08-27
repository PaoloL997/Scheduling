"""Reassign or drop testa/generatore assignments when a unit enters maintenance.

When a physical head or generator is flagged ``in_manutenzione``, incomplete
activities that still hold it are swapped to a compatible free unit using the
same ranking as delay-overlap substitution.  If no spare exists the assignment
is removed so problem detection can raise the usual ``acquisto`` shortfall.
Successful swaps are recorded as resolved notes.
"""

from __future__ import annotations

import logging

from ui.services.problem_detection import (
    _TIPO_TO_CAT,
    _resource_pairs,
    refresh_problems_for_attivita,
)

logger = logging.getLogger(__name__)


def reassign_open_activities(tipo: str, uid: str) -> int:
    """Swap or remove *uid* on incomplete activities that still hold it.

    Args:
        tipo: Resource category (``testa`` or ``generatore``).
        uid: Inventory UID that entered maintenance.

    Returns:
        int: Number of activities whose assignments were changed.
    """
    if tipo not in ("testa", "generatore") or not uid:
        return 0

    from ui.models import Attivita, RisorsaAttivita
    from ui.services.bridge_write import BridgeWriteError, update_attivita
    from ui.services.overlap_reassign import _pairs_to_risorse

    activity_ids = list(
        RisorsaAttivita.objects.filter(tipo=tipo, risorsa_id=uid)
        .exclude(
            attivita__actual_start__isnull=False,
            attivita__actual_finish__isnull=False,
        )
        .values_list("attivita_id", flat=True)
        .distinct()
    )
    changed = 0
    for att_pk in activity_ids:
        try:
            att = Attivita.objects.prefetch_related("risorse").get(pk=att_pk)
        except Attivita.DoesNotExist:
            continue
        if not any(r.tipo == tipo and r.risorsa_id == uid for r in att.risorse.all()):
            continue

        current = list(
            RisorsaAttivita.objects.filter(attivita_id=att.pk).values_list(
                "tipo", "risorsa_id"
            )
        )
        picked = _pick_maintenance_spare(tipo, uid, att)
        replacement_uid = (picked or {}).get("to_uid")
        new_pairs: list[tuple[str, str]] = []
        for res_tipo, res_uid in current:
            if res_tipo == tipo and res_uid == uid:
                if replacement_uid:
                    new_pairs.append((res_tipo, replacement_uid))
                continue
            new_pairs.append((res_tipo, res_uid))

        if not (att.data_inizio and att.data_fine):
            RisorsaAttivita.objects.filter(
                attivita=att, tipo=tipo, risorsa_id=uid
            ).delete()
            if replacement_uid:
                RisorsaAttivita.objects.create(
                    attivita=att, tipo=tipo, risorsa_id=replacement_uid
                )
            refresh_problems_for_attivita(att)
            if picked:
                _persist_maintenance_swap_note(att, tipo, uid, picked)
            changed += 1
            continue

        try:
            update_attivita(
                att.pk,
                {"risorse": _pairs_to_risorse(new_pairs)},
                resolve_overlaps=True,
            )
        except BridgeWriteError:
            logger.warning(
                "Manutenzione: impossibile aggiornare risorse di %s (%s %s).",
                att.activity_id,
                tipo,
                uid,
                exc_info=True,
            )
            dropped = [(t, r) for t, r in current if not (t == tipo and r == uid)]
            try:
                update_attivita(
                    att.pk,
                    {"risorse": _pairs_to_risorse(dropped)},
                    resolve_overlaps=True,
                )
            except BridgeWriteError:
                RisorsaAttivita.objects.filter(
                    attivita=att, tipo=tipo, risorsa_id=uid
                ).delete()
                refresh_problems_for_attivita(att)
            changed += 1
            continue

        if picked:
            _persist_maintenance_swap_note(
                Attivita.objects.get(pk=att.pk), tipo, uid, picked
            )
        changed += 1

    logger.info(
        "Manutenzione %s %s: aggiornate %d attività aperte.", tipo, uid, changed
    )
    return changed


def _persist_maintenance_swap_note(att, tipo: str, from_uid: str, picked: dict) -> None:
    """Store a resolved note for an automatic maintenance substitution."""
    from django.utils import timezone
    from ui.models import ProblemaAttivita
    from ui.services.overlap_reassign import _paren_traits, _resource_name

    to_uid = picked.get("to_uid") or ""
    from_name = _resource_name(picked.get("from_item"), tipo=tipo, uid=from_uid)
    to_name = _resource_name(picked.get("to_item"), tipo=tipo, uid=to_uid)
    descr = (
        f"{from_name} sostituita con {to_name}"
        f"{_paren_traits(picked.get('to_item'))} perché in manutenzione."
    )
    already = ProblemaAttivita.objects.filter(
        attivita=att,
        tipo=ProblemaAttivita.TIPO_MANUTENZIONE,
        risolto=True,
        risorsa_tipo=tipo,
        risorsa_uid=to_uid,
        descrizione=descr,
    ).exists()
    if already:
        return
    ProblemaAttivita.objects.create(
        attivita=att,
        tipo=ProblemaAttivita.TIPO_MANUTENZIONE,
        risorsa_tipo=tipo,
        risorsa_uid=to_uid,
        descrizione=descr,
        risolto=True,
        note_risoluzione="Sostituzione automatica per manutenzione.",
        data_risoluzione=timezone.now(),
    )


def _pick_maintenance_spare(tipo: str, from_uid: str, att) -> dict | None:
    """Return the overlap-style pick dict for a compatible free UID, or ``None``."""
    from ui.models import Attivita
    from ui.services.activity_inventory import _open_assignment_ids
    from ui.services.overlap_reassign import (
        _blocked_availability,
        _pick_alternative,
        occupied_ids_with_delay,
    )

    if not (att.data_inizio and att.data_fine):
        return None

    activities = list(Attivita.objects.prefetch_related("risorse"))
    pairs = {a.pk: _resource_pairs(a) for a in activities}
    occupied = occupied_ids_with_delay(
        tipo, att.data_inizio, att.data_fine, att.pk, activities, pairs
    )
    busy = _open_assignment_ids(tipo, att.pk)
    availability = _blocked_availability(att)
    cat = _TIPO_TO_CAT.get(tipo)
    candidates = (availability or {}).get(cat) if cat else None
    return _pick_alternative(
        tipo=tipo,
        from_uid=from_uid,
        blocked=att,
        occupied=occupied,
        busy=busy,
        candidates=candidates,
    )
