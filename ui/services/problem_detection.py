"""Service for detecting and persisting operational problems on activities.

An *operational problem* is an issue that requires manual intervention before
or during an activity — either a resource that must be physically moved from
another plant (``spostamento``) or a required resource category that has no
assigned item at all (``acquisto``).

Problems are detected at activity-creation time and stored as
:class:`~ui.models.ProblemaAttivita` rows so that planners can track and
resolve them through the UI.  :class:`~ui.models.RisoluzioneProblema` provides
a persistent mirror of resolved notes that survives the dataset reset+reimport
cycle so that resolution history is never lost.

Resource overlaps caused by a late start are detected by
:func:`find_resource_overlaps` and auto-resolved when a compatible
substitute exists; otherwise they are stored as open problems.
"""

from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)

_TIPO_LABELS: dict[str, str] = {
    "testa": "Testa",
    "mandrino": "Mandrino",
    "tastatore": "Tastatore",
    "maschera": "Maschera",
    "generatore": "Generatore",
}

_CAT_KEYS = ("teste", "mandrini", "tastatori", "maschere", "generatori")

_CAT_TO_TIPO: dict[str, str] = {
    "teste": "testa",
    "mandrini": "mandrino",
    "tastatori": "tastatore",
    "maschere": "maschera",
    "generatori": "generatore",
}

_TIPO_TO_CAT: dict[str, str] = {tipo: cat for cat, tipo in _CAT_TO_TIPO.items()}

_SPOSTAMENTO_RESOLVED_NOTE = "Risorsa spostata allo stabilimento di lavorazione."
_STALE_CONDITION_NOTE = "Condizione non piu verificata dopo aggiornamento attivita."


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


_RESOURCE_MODELS: dict[str, type] = {}


def _get_resource_models() -> dict[str, type]:
    """Return a mapping from resource category name to its ORM model class.

    Imports are deferred to avoid circular imports at module load time.

    Returns:
        dict[str, type]: Keys are category names (testa, mandrino, …),
            values are the corresponding ORM model classes.
    """
    if not _RESOURCE_MODELS:
        from ui.models import (
            GeneratoreItem,
            MandrinoItem,
            MascheraItem,
            TastatoreItem,
            TestaItem,
        )

        _RESOURCE_MODELS.update(
            {
                "testa": TestaItem,
                "mandrino": MandrinoItem,
                "tastatore": TastatoreItem,
                "maschera": MascheraItem,
                "generatore": GeneratoreItem,
            }
        )
    return _RESOURCE_MODELS


def _lookup_plant(categoria: str, uid: str) -> str | None:
    """Return the plant name for a single inventory resource UID.

    Args:
        categoria: Resource category name (testa, mandrino, tastatore,
            maschera, generatore).
        uid: The primary-key UID of the resource.

    Returns:
        str | None: The ``plant`` field value, or ``None`` if the UID is not
            found in the inventory.
    """
    model = _get_resource_models().get(categoria)
    if model is None:
        return None
    row = model.objects.filter(uid=uid).values("plant").first()
    return row["plant"] if row else None


def detect_and_save_problems(attivita, risorse_dict: dict[str, list[str]]) -> int:
    """Detect operational problems for an activity and sync open notes.

    Two types of problem are detected:

    * **spostamento** — a selected resource belongs to a plant other than the
      activity's ``stabilimento``.  The resource must be transferred.
    * **acquisto** — fewer resources than the configured minimum were selected
      for a given category.  The missing ones must be sourced externally
      (purchase, rental, or loan).

    Activities flagged ``tp60_custom`` (Sporgenza Tubo-Piastra) have no minimum
    on the consumable categories, so leaving mandrini/tastatori/maschere
    unselected raises no ``acquisto`` note.

    Open ``acquisto`` / ``spostamento`` notes are reconciled with the current
    selection: stale ones are auto-resolved, still-valid ones get an updated
    description when needed, and missing ones are created.  Creation is
    idempotent against **open** signatures only, so a condition that returns
    after an auto-resolve can raise a new open note.  Auto-resolves are not
    mirrored to :class:`~ui.models.RisoluzioneProblema`, so a re-import can
    surface the issue again if it returns.

    Args:
        attivita: The :class:`~ui.models.Attivita` instance.
            Must have ``stabilimento`` and ``pk`` set.
        risorse_dict: Mapping of category name to list of selected UIDs,
            e.g. ``{"teste": ["T1"], "mandrini": ["M1", "M2"], ...}``.
            Accepted keys: ``teste``, ``mandrini``, ``tastatori``,
            ``maschere``, ``generatori``.

    Returns:
        int: The number of new :class:`~ui.models.ProblemaAttivita` rows created.
    """
    from django.utils import timezone
    from ui.models import ProblemaAttivita
    from ui.services.activity_inventory import get_testa_min_requirements

    min_requirements = get_testa_min_requirements(
        (attivita.modello_testa or "").strip() or None,
        bool(getattr(attivita, "tp60_custom", False)),
    )
    modello_testa = (attivita.modello_testa or "").strip()

    stabilimento_attivita = (attivita.stabilimento or "").strip()
    problems: list[ProblemaAttivita] = []

    for key in _CAT_KEYS:
        categoria = _CAT_TO_TIPO[key]
        uids: list[str] = risorse_dict.get(key) or []
        label = _TIPO_LABELS.get(categoria, categoria.capitalize())
        required = min_requirements.get(key, 1)
        selected = len(uids)

        if selected < required:
            problems.append(
                ProblemaAttivita(
                    attivita=attivita,
                    tipo=ProblemaAttivita.TIPO_ACQUISTO,
                    risorsa_tipo=categoria,
                    risorsa_uid="",
                    descrizione=_shortfall_description(
                        label, selected, required, modello_testa
                    ),
                )
            )

        for uid in uids:
            plant = _lookup_plant(categoria, uid)
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

    open_types = (
        ProblemaAttivita.TIPO_ACQUISTO,
        ProblemaAttivita.TIPO_SPOSTAMENTO,
    )
    for problema in ProblemaAttivita.objects.filter(
        attivita=attivita,
        tipo__in=open_types,
        risolto=False,
    ):
        sig = (problema.tipo, problema.risorsa_tipo, problema.risorsa_uid)
        if sig not in current_sigs:
            still_assigned = False
            if problema.tipo == ProblemaAttivita.TIPO_SPOSTAMENTO:
                cat = _TIPO_TO_CAT.get(problema.risorsa_tipo)
                still_assigned = bool(
                    cat and problema.risorsa_uid in (risorse_dict.get(cat) or [])
                )
            problema.risolto = True
            problema.note_risoluzione = (
                _SPOSTAMENTO_RESOLVED_NOTE if still_assigned else _STALE_CONDITION_NOTE
            )
            problema.data_risoluzione = timezone.now()
            problema.save(
                update_fields=["risolto", "note_risoluzione", "data_risoluzione"]
            )
            continue

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

    open_sigs: set[tuple[str, str, str]] = set(
        ProblemaAttivita.objects.filter(
            attivita=attivita,
            tipo__in=open_types,
            risolto=False,
        ).values_list("tipo", "risorsa_tipo", "risorsa_uid")
    )
    new_problems = [
        p for p in problems if (p.tipo, p.risorsa_tipo, p.risorsa_uid) not in open_sigs
    ]

    if new_problems:
        ProblemaAttivita.objects.bulk_create(new_problems)

    return len(new_problems)


def _risorse_dict_for_attivita(attivita) -> dict[str, list[str]]:
    """Build the wizard-style risorse mapping from assigned inventory rows."""
    risorse_dict: dict[str, list[str]] = {}
    for r in attivita.risorse.all():
        cat = _TIPO_TO_CAT.get(r.tipo)
        if cat:
            risorse_dict.setdefault(cat, []).append(r.risorsa_id)
    return risorse_dict


def refresh_problems_for_attivita(attivita) -> int:
    """Re-run problem detection from the activity's current assignments."""
    return detect_and_save_problems(attivita, _risorse_dict_for_attivita(attivita))


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
    """Return the expected finish if the activity started late (or at all).

    Uses planned duration ``data_fine - data_inizio`` applied to
    ``actual_start``.  Missing dates yield ``None``.
    """
    if not (attivita.actual_start and attivita.data_inizio and attivita.data_fine):
        return None
    return attivita.actual_start + (attivita.data_fine - attivita.data_inizio)


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


def find_resource_overlaps(activities: list | None = None) -> dict[int, list[dict]]:
    """Compute delay-caused resource overlaps without persisting notes.

    Activity **X** (has ``actual_start``) occupies its resources until
    ``effective_finish(X)``.  Activity **Y** conflicts when it is distinct,
    starts on or after ``X.data_inizio``, starts before that effective
    finish, and shares at least one assigned resource.

    The visual window is
    ``[Y.data_inizio, min(Y.data_fine, effective_finish(X))]``.
    The same payload is attached to both activities (``role`` is ``late``
    on X and ``blocked`` on Y).

    Args:
        activities: Optional prefetched :class:`~ui.models.Attivita` list.
            When omitted, all activities are loaded.

    Returns:
        dict[int, list[dict]]: Mapping of activity primary key to overlap
        descriptors.  Activities without overlaps are omitted.
    """
    from ui.models import Attivita

    if activities is None:
        activities = list(Attivita.objects.prefetch_related("risorse"))

    pairs = {a.pk: _resource_pairs(a) for a in activities}
    result: dict[int, list[dict]] = {}
    seen_pairs: set[tuple[int, int]] = set()

    for late in activities:
        fine = effective_finish(late)
        if fine is None or not late.data_inizio:
            continue
        late_pairs = pairs[late.pk]
        if not late_pairs:
            continue
        for blocked in activities:
            if blocked.pk == late.pk or not blocked.data_inizio:
                continue
            if blocked.data_inizio < late.data_inizio or blocked.data_inizio >= fine:
                continue
            # Same planned start and both started: emit the pair once.
            if (
                blocked.actual_start
                and blocked.data_inizio == late.data_inizio
                and blocked.pk < late.pk
            ):
                continue
            pair_key = (min(late.pk, blocked.pk), max(late.pk, blocked.pk))
            if pair_key in seen_pairs:
                continue
            shared = late_pairs & pairs[blocked.pk]
            if not shared:
                continue
            seen_pairs.add(pair_key)
            overlap_end = fine
            if blocked.data_fine:
                overlap_end = min(blocked.data_fine, fine)
            resources = [
                _serialize_overlap_resource(tipo, uid) for tipo, uid in sorted(shared)
            ]
            base = {
                "resources": resources,
                "overlap_start": blocked.data_inizio.isoformat(),
                "overlap_end": overlap_end.isoformat(),
            }
            result.setdefault(late.pk, []).append(
                {**base, **_peer_identity(blocked), "role": "late"}
            )
            result.setdefault(blocked.pk, []).append(
                {**base, **_peer_identity(late), "role": "blocked"}
            )
    return result


def detect_for_all_activities() -> int:
    """Run problem detection for every activity in the database.

    Called after a dataset re-import and on Pianificazione page load so that
    open ``acquisto`` / ``spostamento`` notes stay aligned with current
    assignments.  Stale open notes are auto-resolved; new conditions create
    fresh rows.  Delay overlaps are auto-swapped when possible; remaining
    ones become open ``sovrapposizione`` problems.  Resolved swap notes
    are kept.

    Returns:
        int: Total number of new :class:`~ui.models.ProblemaAttivita` rows
            created across all activities.
    """
    from ui.models import Attivita
    from ui.services.overlap_reassign import auto_resolve_resource_overlaps

    auto_resolve_resource_overlaps()

    total = 0
    activitas = Attivita.objects.prefetch_related("risorse").all()
    for a in activitas:
        total += refresh_problems_for_attivita(a)

    logger.info("detect_for_all_activities: %d nuovi problemi rilevati.", total)
    return total


def restore_resolved_notes() -> int:
    """Re-apply persistent resolution notes to freshly imported problems.

    After a dataset reset+reimport, :class:`~ui.models.ProblemaAttivita` rows
    are re-created by :func:`detect_for_all_activities`.  Manual resolutions
    of ``acquisto`` / ``spostamento`` are no longer restored: those notes
    close only when the underlying condition is gone.

    Returns:
        int: Number of problems restored as resolved.
    """
    from django.utils import timezone
    from ui.models import Attivita, ProblemaAttivita, RisoluzioneProblema

    resoluzioni = list(RisoluzioneProblema.objects.all())
    if not resoluzioni:
        return 0

    restored = 0
    skipped_types = {
        ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
        ProblemaAttivita.TIPO_SPOSTAMENTO,
        ProblemaAttivita.TIPO_ACQUISTO,
        ProblemaAttivita.TIPO_MANUTENZIONE,
    }
    for r in resoluzioni:
        if r.tipo in skipped_types:
            continue
        try:
            attivita = Attivita.objects.get(
                commessa=r.commessa,
                item=r.item,
                activity=r.activity,
            )
        except Attivita.DoesNotExist:
            continue

        problema = ProblemaAttivita.objects.filter(
            attivita=attivita,
            tipo=r.tipo,
            risorsa_tipo=r.risorsa_tipo,
            risorsa_uid=r.risorsa_uid,
        ).first()

        if problema is None or problema.risolto:
            continue

        problema.risolto = True
        problema.note_risoluzione = r.note_risoluzione
        problema.data_risoluzione = r.data_risoluzione or timezone.now()
        problema.save(update_fields=["risolto", "note_risoluzione", "data_risoluzione"])
        restored += 1

    logger.info("restore_resolved_notes: %d risoluzioni ripristinate.", restored)
    return restored
