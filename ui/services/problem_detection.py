"""Service for detecting and persisting operational problems on activities.

An *operational problem* is an issue that requires manual intervention before
or during an activity — either a resource that must be physically moved from
another plant (``spostamento``) or a required resource category that has no
assigned item at all (``acquisto``).

Problems are detected when an activity is written and stored as
:class:`~ui.models.ProblemaAttivita` rows so that planners can track and
resolve them through the UI.

Resource overlaps caused by a late start are detected by
:func:`find_resource_overlaps` and auto-resolved when a compatible
substitute exists; otherwise they are stored as open problems.

:func:`resource_shortfalls` is the single definition of "below the required
minimum": it feeds both the ``acquisto`` notes and the guard that refuses to
record the actual start of an under-equipped activity.
"""

from __future__ import annotations

import logging
from datetime import date

from ui.services.resource_types import (
    CATEGORIA_TO_TIPO as _CAT_TO_TIPO,
    CATEGORIE as _CAT_KEYS,
    TIPO_LABELS as _TIPO_LABELS,
    TIPO_TO_CATEGORIA as _TIPO_TO_CAT,
    model_for_tipo,
    pairs_to_risorse_dict,
)

logger = logging.getLogger(__name__)

_SPOSTAMENTO_RESOLVED_NOTE = "Risorsa spostata allo stabilimento di lavorazione."
_STALE_CONDITION_NOTE = "Condizione non piu verificata dopo aggiornamento attivita."
_RITARDO_RESOLVED_NOTE = "Attivita avviata o ripianificata."
_COMPLETED_NOTE = (
    "Attivita conclusa: la posizione attuale delle risorse non la riguarda piu."
)


def _shortfall_description(
    label: str,
    selected: int,
    required: int,
    modello_testa: str,
) -> str:
    """Build an Italian description for an under-selected resource category."""
    modello_part = f" per il modello {modello_testa}" if modello_testa else ""
    if label in ("Testa", "Maschera"):
        sel_adj = "selezionata" if selected == 1 else "selezionate"
    else:
        sel_adj = "selezionato" if selected == 1 else "selezionati"

    base = f"{label}: {selected}/{required} {sel_adj}{modello_part}"
    if selected == 0:
        return f"{base} — da recuperare, acquistare o affittare."
    mancanti = required - selected
    return f"{base} — manca/no ancora {mancanti}."


def resource_shortfalls(
    modello_testa: str | None,
    counts: dict[str, int],
    tp60_custom: bool = False,
    *,
    configs: dict[str, object] | None = None,
) -> list[dict]:
    """Return one descriptor per category selected below its required minimum.

    The minimums come from
    :func:`~ui.services.activity_inventory.get_testa_min_requirements`: one
    testa and ``min_generatori`` generators, plus the per-testa-model minimum
    of each accessory (all default to ``1`` when the model has no
    :class:`~ui.models.ConfigurazioneTesta` row).

    Args:
        modello_testa: Testa model of the activity, used for the config lookup.
        counts: Number of selected resources keyed by wizard category
            (``teste``, ``mandrini``, ``tastatori``, ``maschere``,
            ``generatori``).  Missing keys count as zero.
        tp60_custom: When ``True`` the consumable minimums are the fixed
            TP60 Custom ones (2 mandrini, 1 maschera, no tastatore), per
            :func:`~ui.services.activity_inventory.get_testa_min_requirements`.
        configs: Preloaded testa configurations from
            :func:`~ui.services.activity_inventory.load_testa_configs`, to
            avoid one query per call.

    Returns:
        list[dict]: One dict per under-selected category, in
            :data:`_CAT_KEYS` order, with keys ``categoria`` (wizard key),
            ``tipo`` (resource category), ``label``, ``selezionati``,
            ``richiesti`` and ``descrizione``.
    """
    from ui.services.activity_inventory import get_testa_min_requirements

    modello = (modello_testa or "").strip()
    minimi = get_testa_min_requirements(modello or None, tp60_custom, configs=configs)

    shortfalls: list[dict] = []
    for key in _CAT_KEYS:
        categoria = _CAT_TO_TIPO[key]
        required = minimi.get(key, 1)
        selected = counts.get(key, 0)
        if selected >= required:
            continue
        label = _TIPO_LABELS.get(categoria, categoria.capitalize())
        shortfalls.append(
            {
                "categoria": key,
                "tipo": categoria,
                "label": label,
                "selezionati": selected,
                "richiesti": required,
                "descrizione": _shortfall_description(
                    label, selected, required, modello
                ),
            }
        )
    return shortfalls


def _counts_by_categoria(pairs) -> dict[str, int]:
    """Count distinct resource UIDs per wizard category.

    Args:
        pairs: Iterable of ``(tipo, risorsa_id)`` assignments.

    Returns:
        dict[str, int]: One entry per key of :data:`_CAT_KEYS`.
    """
    seen: dict[str, set[str]] = {key: set() for key in _CAT_KEYS}
    for tipo, uid in pairs:
        cat = _TIPO_TO_CAT.get(tipo)
        if cat and uid:
            seen[cat].add(uid)
    return {key: len(uids) for key, uids in seen.items()}


def missing_minimum_resources(
    attivita, pairs=None, *, configs: dict[str, object] | None = None
) -> list[dict]:
    """Return the shortfalls that forbid starting *attivita*.

    An activity may not be started — that is, ``actual_start`` may not be
    recorded — until the minimum set of resources is booked.  The rule is the
    same one that raises the ``acquisto`` notes, so an activity can be started
    exactly when it has no open shortfall.

    Args:
        attivita: The :class:`~ui.models.Attivita` being started.
        pairs: ``(tipo, risorsa_id)`` assignments to evaluate; defaults to the
            rows currently booked for the activity.
        configs: Preloaded testa configurations from
            :func:`~ui.services.activity_inventory.load_testa_configs`.

    Returns:
        list[dict]: Shortfall descriptors from :func:`resource_shortfalls`;
            empty when the activity may be started.
    """
    if pairs is None:
        pairs = [(r.tipo, r.risorsa_id) for r in attivita.risorse.all()]
    return resource_shortfalls(
        attivita.modello_testa,
        _counts_by_categoria(pairs),
        tp60_custom=bool(getattr(attivita, "tp60_custom", False)),
        configs=configs,
    )


def format_missing_minimum_message(
    shortfalls: list[dict],
    *,
    action_hint: str | None = None,
) -> str:
    """Build the Italian message refusing the start of an under-equipped activity.

    Args:
        shortfalls: Descriptors as returned by :func:`missing_minimum_resources`.
        action_hint: Optional closing sentence guiding the user.

    Returns:
        str: A descriptive error message; falls back to a generic sentence
            when *shortfalls* is empty.
    """
    if not shortfalls:
        return "Risorse minime non soddisfatte."

    hint = action_hint or (
        "Assegna le risorse mancanti con «Modifica risorse» prima di "
        "registrare l'inizio effettivo."
    )
    detail = "; ".join(
        f"{s['label']} {s['selezionati']}/{s['richiesti']}" for s in shortfalls
    )
    return (
        f"Impossibile avviare l'attività: mancano le risorse minime ({detail}). {hint}"
    )


def _lookup_plants(categoria: str, uids: list[str]) -> dict[str, str | None]:
    """Return ``{uid: plant}`` for the given inventory UIDs in one query.

    Args:
        categoria: Resource category name (testa, mandrino, tastatore,
            maschera, generatore).
        uids: Primary-key UIDs to look up.

    Returns:
        dict[str, str | None]: Plant per UID; UIDs missing from the
            inventory are absent.
    """
    if categoria not in _TIPO_TO_CAT or not uids:
        return {}
    model = model_for_tipo(categoria)
    return dict(model.objects.filter(uid__in=uids).values_list("uid", "plant"))


def detect_and_save_problems(
    attivita,
    risorse_dict: dict[str, list[str]],
    *,
    plants: dict[tuple[str, str], str | None] | None = None,
    open_problems: list | None = None,
    configs: dict[str, object] | None = None,
) -> int:
    """Detect operational problems for an activity and sync open notes.

    Two types of problem are detected:

    * **spostamento** — a selected resource belongs to a plant other than the
      activity's ``stabilimento``.  The resource must be transferred.
    * **acquisto** — fewer resources than the configured minimum were selected
      for a given category.  The missing ones must be sourced externally
      (purchase, rental, or loan).

    Activities flagged ``tp60_custom`` (Sporgenza Tubo-Piastra) need 2
    mandrini and 1 maschera whatever the testa configuration says, and no
    tastatore: leaving the tastatore unselected raises no ``acquisto`` note.

    A completed activity (``actual_finish`` set) raises neither: where its
    resources are *now* no longer concerns it, so moving a resource from the
    plant after the work is done opens nothing, and any open note is
    auto-resolved as "attivita conclusa".

    Open ``acquisto`` / ``spostamento`` notes are reconciled with the current
    selection: stale ones are auto-resolved, still-valid ones get an updated
    description when needed, and missing ones are created.  Creation is
    idempotent against **open** signatures only, so a condition that returns
    after an auto-resolve raises a new open note.

    Args:
        attivita: The :class:`~ui.models.Attivita` instance.
            Must have ``stabilimento`` and ``pk`` set.
        risorse_dict: Mapping of category name to list of selected UIDs,
            e.g. ``{"teste": ["T1"], "mandrini": ["M1", "M2"], ...}``.
            Accepted keys: ``teste``, ``mandrini``, ``tastatori``,
            ``maschere``, ``generatori``.
        plants: Preloaded ``(categoria, uid) -> plant`` from
            :func:`_load_plants`; queried per category when omitted.
        open_problems: Preloaded open ``acquisto`` / ``spostamento`` rows
            of *attivita*; queried when omitted.
        configs: Preloaded testa configurations from
            :func:`~ui.services.activity_inventory.load_testa_configs`.

    Returns:
        int: The number of new :class:`~ui.models.ProblemaAttivita` rows created.
    """
    from django.utils import timezone
    from ui.models import ProblemaAttivita

    open_types = (
        ProblemaAttivita.TIPO_ACQUISTO,
        ProblemaAttivita.TIPO_SPOSTAMENTO,
    )
    if open_problems is None:
        open_problems = list(
            ProblemaAttivita.objects.filter(
                attivita=attivita,
                tipo__in=open_types,
                risolto=False,
            )
        )

    completed = bool(getattr(attivita, "actual_finish", None))
    shortfalls = {
        s["categoria"]: s
        for s in resource_shortfalls(
            attivita.modello_testa,
            {key: len(risorse_dict.get(key) or []) for key in _CAT_KEYS},
            tp60_custom=bool(getattr(attivita, "tp60_custom", False)),
            configs=configs,
        )
    }

    stabilimento_attivita = (attivita.stabilimento or "").strip()
    problems: list[ProblemaAttivita] = []

    for key in () if completed else _CAT_KEYS:  # a finished activity raises nothing
        categoria = _CAT_TO_TIPO[key]
        uids: list[str] = risorse_dict.get(key) or []
        label = _TIPO_LABELS.get(categoria, categoria.capitalize())

        shortfall = shortfalls.get(key)
        if shortfall is not None:
            problems.append(
                ProblemaAttivita(
                    attivita=attivita,
                    tipo=ProblemaAttivita.TIPO_ACQUISTO,
                    risorsa_tipo=categoria,
                    risorsa_uid="",
                    descrizione=shortfall["descrizione"],
                )
            )

        if plants is None:
            plant_of = _lookup_plants(categoria, uids)
        else:
            plant_of = {uid: plants.get((categoria, uid)) for uid in uids}
        for uid in uids:
            plant = plant_of.get(uid)
            if (
                plant
                and stabilimento_attivita
                and plant.strip() != stabilimento_attivita
            ):
                problems.append(
                    ProblemaAttivita(
                        attivita=attivita,
                        tipo=ProblemaAttivita.TIPO_SPOSTAMENTO,
                        risorsa_tipo=categoria,
                        risorsa_uid=uid,
                        stabilimento_origine=plant,
                        descrizione=(
                            f"{label} {uid} si trova presso {plant} "
                            f"e deve essere spostato/a a {stabilimento_attivita}."
                        ),
                    )
                )

    current_by_sig: dict[tuple[str, str, str], ProblemaAttivita] = {
        (p.tipo, p.risorsa_tipo, p.risorsa_uid): p for p in problems
    }
    current_sigs = set(current_by_sig)

    open_sigs: set[tuple[str, str, str]] = set()
    for problema in open_problems:
        sig = (problema.tipo, problema.risorsa_tipo, problema.risorsa_uid)
        if sig not in current_sigs:
            still_assigned = False
            if problema.tipo == ProblemaAttivita.TIPO_SPOSTAMENTO:
                cat = _TIPO_TO_CAT.get(problema.risorsa_tipo)
                still_assigned = bool(
                    cat and problema.risorsa_uid in (risorse_dict.get(cat) or [])
                )
            problema.risolto = True
            if completed:
                problema.note_risoluzione = _COMPLETED_NOTE
            elif still_assigned:
                problema.note_risoluzione = _SPOSTAMENTO_RESOLVED_NOTE
            else:
                problema.note_risoluzione = _STALE_CONDITION_NOTE
            problema.data_risoluzione = timezone.now()
            problema.save(
                update_fields=["risolto", "note_risoluzione", "data_risoluzione"]
            )

            from ui.services.operation_log import record_operation

            record_operation(
                source="sistema",
                azione="sistema_problema_risolto",
                descrizione=f"Problema risolto automaticamente: {problema.descrizione}",
                riferimento=f"{attivita.commessa}/{attivita.item}/{attivita.activity}",
            )
            continue

        open_sigs.add(sig)
        desired = current_by_sig[sig]
        updates: list[str] = []
        if problema.descrizione != desired.descrizione:
            problema.descrizione = desired.descrizione
            updates.append("descrizione")
        if (
            desired.stabilimento_origine
            and problema.stabilimento_origine != desired.stabilimento_origine
        ):
            problema.stabilimento_origine = desired.stabilimento_origine
            updates.append("stabilimento_origine")
        if updates:
            problema.save(update_fields=updates)

    new_problems = [
        p for p in problems if (p.tipo, p.risorsa_tipo, p.risorsa_uid) not in open_sigs
    ]

    if new_problems:
        ProblemaAttivita.objects.bulk_create(new_problems)

    return len(new_problems)


def _risorse_dict_for_attivita(attivita) -> dict[str, list[str]]:
    """Build the wizard-style risorse mapping from assigned inventory rows."""
    return pairs_to_risorse_dict((r.tipo, r.risorsa_id) for r in attivita.risorse.all())


def refresh_problems_for_attivita(attivita, **preloaded) -> int:
    """Re-run problem detection from the activity's current assignments.

    Args:
        attivita: The :class:`~ui.models.Attivita` instance.
        **preloaded: ``plants`` / ``open_problems`` / ``configs`` forwarded
            to :func:`detect_and_save_problems`.
    """
    return detect_and_save_problems(
        attivita, _risorse_dict_for_attivita(attivita), **preloaded
    )


def _load_plants(activities) -> dict[tuple[str, str], str | None]:
    """Return the plant of every resource assigned to *activities*.

    One query per resource category instead of one per activity.

    Args:
        activities: :class:`~ui.models.Attivita` instances with ``risorse``
            prefetched.

    Returns:
        dict: ``(categoria, uid)`` → ``plant``; UIDs missing from the
            inventory are absent.
    """
    uids_by_categoria: dict[str, set[str]] = {}
    for attivita in activities:
        for r in attivita.risorse.all():
            if r.tipo in _TIPO_TO_CAT and r.risorsa_id:
                uids_by_categoria.setdefault(r.tipo, set()).add(r.risorsa_id)

    plants: dict[tuple[str, str], str | None] = {}
    for categoria, uids in uids_by_categoria.items():
        for uid, plant in _lookup_plants(categoria, sorted(uids)).items():
            plants[(categoria, uid)] = plant
    return plants


def refresh_problems_for_activities(activities) -> int:
    """Refresh ``acquisto`` / ``spostamento`` for many activities at once.

    Testa configurations, resource plants and open notes are loaded in a
    handful of queries, whatever the number of activities.

    Args:
        activities: :class:`~ui.models.Attivita` instances with ``risorse``
            prefetched.

    Returns:
        int: Number of new :class:`~ui.models.ProblemaAttivita` rows created.
    """
    from ui.models import ProblemaAttivita
    from ui.services.activity_inventory import load_testa_configs

    activities = list(activities)
    if not activities:
        return 0
    configs = load_testa_configs()
    plants = _load_plants(activities)
    open_by_attivita: dict[int, list] = {}
    for problema in ProblemaAttivita.objects.filter(
        attivita__in=[a.pk for a in activities],
        tipo__in=(
            ProblemaAttivita.TIPO_ACQUISTO,
            ProblemaAttivita.TIPO_SPOSTAMENTO,
        ),
        risolto=False,
    ):
        open_by_attivita.setdefault(problema.attivita_id, []).append(problema)

    total = 0
    for a in activities:
        total += refresh_problems_for_attivita(
            a,
            plants=plants,
            open_problems=open_by_attivita.get(a.pk, []),
            configs=configs,
        )
    return total


def refresh_problems_for_resource(tipo: str, *uids: str) -> int:
    """Re-run problem detection for activities assigned any of the given UIDs.

    Args:
        tipo: Resource category (``testa``, ``mandrino``, …).
        uids: Inventory UIDs that changed (plant and/or identity).

    Returns:
        int: Number of new problem rows created across matching activities.
    """
    from ui.models import RisorsaAttivita

    ids = [uid for uid in uids if uid]
    if not tipo or not ids:
        return 0

    rows = (
        RisorsaAttivita.objects.filter(tipo=tipo, risorsa_id__in=ids)
        .select_related("attivita")
        .prefetch_related("attivita__risorse")
    )
    seen: set[int] = set()
    total = 0
    for row in rows:
        if row.attivita_id in seen:
            continue
        seen.add(row.attivita_id)
        total += refresh_problems_for_attivita(row.attivita)
    return total


def effective_finish(attivita) -> date | None:
    """Return the expected finish of a started activity.

    The planned duration is counted in **working days** (Monday to Friday,
    inclusive of both planned dates, at least one), the same unit the
    scheduler plans in, and laid out from ``actual_start``: starting two days
    late on a Thursday ends on the following Tuesday, not on Saturday.
    Missing dates yield ``None``.
    """
    if not (attivita.actual_start and attivita.data_inizio and attivita.data_fine):
        return None
    from src.optimization.calendar import (
        WorkingCalendar,
        working_days_in_calendar_span,
    )

    duration = working_days_in_calendar_span(attivita.data_inizio, attivita.data_fine)
    return WorkingCalendar(attivita.actual_start).completion_date(0, duration)


def _resource_pairs(attivita) -> set[tuple[str, str]]:
    """Return the assigned ``(tipo, uid)`` pairs for an activity."""
    return {(r.tipo, r.risorsa_id) for r in attivita.risorse.all()}


def _serialize_overlap_resource(tipo: str, uid: str) -> dict:
    """Return a display dict for one shared resource."""
    label = _TIPO_LABELS.get(tipo, tipo.capitalize())
    return {"tipo": tipo, "uid": uid, "label": f"{label} {uid}"}


def _peer_identity(attivita) -> dict:
    """Return identity fields for the other activity in an overlap."""
    return {
        "peer_orm_id": attivita.pk,
        "peer_activity_id": attivita.activity_id,
        "peer_label": attivita.activity or str(attivita),
        "peer_commessa": attivita.commessa,
        "peer_item": attivita.item,
        "peer_activity": attivita.activity or "",
    }


def _overlap_roles(first, second) -> tuple:
    """Return ``(late, blocked)`` for an overlapping pair.

    *blocked* is the activity that can still be moved: the one that has not
    started.  When both have started (or neither has), the one planned to
    start later is the blocked side; ``pk`` breaks a remaining tie so the
    pair is always ordered deterministically.
    """
    first_started = bool(first.actual_start)
    second_started = bool(second.actual_start)
    if first_started != second_started:
        return (first, second) if first_started else (second, first)

    first_start = first.data_inizio or date.max
    second_start = second.data_inizio or date.max
    if first_start != second_start:
        return (first, second) if first_start < second_start else (second, first)
    return (first, second) if first.pk < second.pk else (second, first)


def find_resource_overlaps(
    activities: list | None = None, today: date | None = None
) -> dict[int, list[dict]]:
    """Compute resource overlaps from the shared occupancy windows.

    Two activities conflict when their
    :func:`~ui.services.resource_conflicts.occupation_window` intervals
    intersect and they share at least one assigned resource.  That single
    definition covers every case: a delayed activity holding a resource past
    its planned end, a completed one releasing it at ``actual_finish``, and a
    never-started one still holding its planned window.

    The same payload is attached to both activities, with ``role`` set to
    ``late`` on the reserving side and ``blocked`` on the side that should
    move (see :func:`_overlap_roles`).  Pairs whose blocked side is already
    finished are skipped: there is nothing left to reschedule.

    Args:
        activities: Optional prefetched :class:`~ui.models.Attivita` list.
            When omitted, all activities are loaded.
        today: Reference day for the occupancy of running activities.

    Returns:
        dict[int, list[dict]]: Mapping of activity primary key to overlap
        descriptors.  Activities without overlaps are omitted.
    """
    from ui.models import Attivita
    from ui.services.resource_conflicts import occupation_window

    if activities is None:
        activities = list(Attivita.objects.prefetch_related("risorse"))

    pairs = {a.pk: _resource_pairs(a) for a in activities}
    windows: dict[int, tuple[date, date]] = {}
    for att in activities:
        if not pairs[att.pk]:
            continue
        window = occupation_window(att, today=today)
        if window is not None:
            windows[att.pk] = window

    ordered = [a for a in activities if a.pk in windows]
    result: dict[int, list[dict]] = {}

    for index, first in enumerate(ordered):
        first_start, first_end = windows[first.pk]
        for second in ordered[index + 1 :]:
            second_start, second_end = windows[second.pk]
            if first_start > second_end or second_start > first_end:
                continue
            shared = pairs[first.pk] & pairs[second.pk]
            if not shared:
                continue
            late, blocked = _overlap_roles(first, second)
            if blocked.actual_finish:
                continue
            resources = [
                _serialize_overlap_resource(tipo, uid) for tipo, uid in sorted(shared)
            ]
            base = {
                "resources": resources,
                "overlap_start": max(first_start, second_start).isoformat(),
                "overlap_end": min(first_end, second_end).isoformat(),
            }
            result.setdefault(late.pk, []).append(
                {**base, **_peer_identity(blocked), "role": "late"}
            )
            result.setdefault(blocked.pk, []).append(
                {**base, **_peer_identity(late), "role": "blocked"}
            )
    return result


def overdue_not_started(attivita, today: date | None = None) -> bool:
    """Return whether an activity is past its planned finish and never started.

    Args:
        attivita: The :class:`~ui.models.Attivita` to test.
        today: Reference date; defaults to :meth:`datetime.date.today`.

    Returns:
        bool: ``True`` when no actual date is set and ``data_fine`` is in the
            past.
    """
    if attivita.actual_start or attivita.actual_finish:
        return False
    if not attivita.data_fine:
        return False
    return attivita.data_fine < (today or date.today())


def _overdue_description(attivita) -> str:
    """Italian description for a never-started, past-due activity."""
    return (
        "Non iniziata: la fine prevista era il "
        f"{attivita.data_fine.strftime('%d/%m/%Y')}. Le risorse restano "
        "prenotate finche' l'attivita' non viene avviata o ripianificata."
    )


def sync_overdue_problems(
    activities: list | None = None,
    today: date | None = None,
) -> int:
    """Create or close ``ritardo`` notes for never-started, past-due activities.

    The condition is re-evaluated on every call: a note closes as soon as the
    activity is started or its planned window is moved forward, mirroring the
    reconciliation used for ``acquisto`` / ``spostamento``.

    Args:
        activities: Optional prefetched :class:`~ui.models.Attivita` list.
        today: Reference date; defaults to :meth:`datetime.date.today`.

    Returns:
        int: Number of new :class:`~ui.models.ProblemaAttivita` rows created.
    """
    from django.utils import timezone
    from ui.models import Attivita, ProblemaAttivita

    if activities is None:
        activities = list(Attivita.objects.all())
    reference = today or date.today()

    desired = {att.pk: att for att in activities if overdue_not_started(att, reference)}

    now = timezone.now()
    seen: set[int] = set()
    for problema in ProblemaAttivita.objects.filter(
        tipo=ProblemaAttivita.TIPO_RITARDO,
        risolto=False,
    ):
        att = desired.get(problema.attivita_id)
        if att is None:
            problema.risolto = True
            problema.note_risoluzione = _RITARDO_RESOLVED_NOTE
            problema.data_risoluzione = now
            problema.save(
                update_fields=["risolto", "note_risoluzione", "data_risoluzione"]
            )

            from ui.services.operation_log import record_operation

            resolved_att = problema.attivita
            record_operation(
                source="sistema",
                azione="sistema_problema_risolto",
                descrizione=f"Ritardo risolto: {problema.descrizione}",
                riferimento=(
                    f"{resolved_att.commessa}/{resolved_att.item}/"
                    f"{resolved_att.activity}"
                ),
            )
            continue
        seen.add(problema.attivita_id)
        descrizione = _overdue_description(att)
        if problema.descrizione != descrizione:
            problema.descrizione = descrizione
            problema.bloccata_fino = att.data_fine
            problema.save(update_fields=["descrizione", "bloccata_fino"])

    to_create = [
        ProblemaAttivita(
            attivita=att,
            tipo=ProblemaAttivita.TIPO_RITARDO,
            risorsa_tipo="",
            risorsa_uid="",
            descrizione=_overdue_description(att),
            bloccata_fino=att.data_fine,
        )
        for pk, att in desired.items()
        if pk not in seen
    ]
    if to_create:
        ProblemaAttivita.objects.bulk_create(to_create)

    return len(to_create)


def detect_for_all_activities() -> int:
    """Run problem detection for every activity in the database.

    The full pass behind :func:`ui.services.reconcile.reconcile` with no
    activity filter: open ``acquisto`` / ``spostamento`` notes are aligned
    with the current assignments (stale ones auto-resolved, new conditions
    created), delay overlaps are auto-swapped when possible and the rest
    become open ``sovrapposizione`` problems, never-started activities past
    their planned finish raise a ``ritardo`` note (both inside
    :func:`~ui.services.overlap_reassign.auto_resolve_resource_overlaps`).
    Resolved swap notes are kept.  Never called by a read path.

    Returns:
        int: Total number of new :class:`~ui.models.ProblemaAttivita` rows
            created across all activities.
    """
    from ui.models import Attivita
    from ui.services.overlap_reassign import auto_resolve_resource_overlaps

    auto_resolve_resource_overlaps()

    total = refresh_problems_for_activities(
        Attivita.objects.prefetch_related("risorse").all()
    )

    logger.info("detect_for_all_activities: %d nuovi problemi rilevati.", total)
    return total
