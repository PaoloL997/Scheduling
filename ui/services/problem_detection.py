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
"""

from __future__ import annotations

import logging

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
    """Detect operational problems for an activity and persist new ones.

    Two types of problem are detected:

    * **spostamento** — a selected resource belongs to a plant other than the
      activity's ``stabilimento``.  The resource must be transferred.
    * **acquisto** — no resource was selected for a given category.  One must
      be sourced externally (purchase, rental, or loan).

    This function is **idempotent**: problems whose signature
    (tipo + risorsa_tipo + risorsa_uid) already exists for the activity are
    skipped, so calling it multiple times will not create duplicates.

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
    from ui.models import ProblemaAttivita
    from ui.services.activity_inventory import get_testa_min_requirements

    min_requirements = get_testa_min_requirements(
        (attivita.modello_testa or "").strip() or None
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

    if not problems:
        return 0

    # Idempotency: skip problems whose signature already exists for this activity.
    existing_sigs: set[tuple[str, str, str]] = set(
        ProblemaAttivita.objects.filter(attivita=attivita).values_list(
            "tipo", "risorsa_tipo", "risorsa_uid"
        )
    )
    new_problems = [
        p
        for p in problems
        if (p.tipo, p.risorsa_tipo, p.risorsa_uid) not in existing_sigs
    ]

    if new_problems:
        ProblemaAttivita.objects.bulk_create(new_problems)

    return len(new_problems)


def _format_date_it(value) -> str:
    """Format a date as ``dd/mm/YYYY`` for Italian-language messages."""
    return value.strftime("%d/%m/%Y") if value else "?"


def effective_finish(attivita):
    """Return the effective finish date of an activity, or ``None``.

    The effective finish is the actual start date shifted by the planned
    calendar-day span (``data_fine - data_inizio``), i.e. the estimated
    duration applied from the moment the activity actually started.

    Args:
        attivita: The :class:`~ui.models.Attivita` instance.

    Returns:
        datetime.date | None: The effective finish date, or ``None`` when
            ``actual_start``, ``data_inizio`` or ``data_fine`` is missing.
    """
    if not (attivita.actual_start and attivita.data_inizio and attivita.data_fine):
        return None
    span = attivita.data_fine - attivita.data_inizio
    return attivita.actual_start + span


def _encode_blocked_resources(blocked: list[tuple[str, str]]) -> str:
    """Serialize blocked resource pairs for storage in ``risorsa_uid``."""
    return "|".join(f"{tipo}:{uid}" for tipo, uid in blocked)


def parse_blocked_resources(
    risorsa_uid: str, risorsa_tipo: str = ""
) -> list[tuple[str, str]]:
    """Parse blocked ``(tipo, uid)`` pairs from a problem record.

    Supports the consolidated pipe-separated encoding (``testa:T1|mandrino:M1``)
    and the legacy single-resource format.
    """
    if risorsa_uid and ("|" in risorsa_uid or ":" in risorsa_uid):
        pairs: list[tuple[str, str]] = []
        for part in risorsa_uid.split("|"):
            if ":" not in part:
                continue
            tipo, uid = part.split(":", 1)
            if tipo and uid:
                pairs.append((tipo, uid))
        return pairs
    if risorsa_tipo and risorsa_uid:
        return [(risorsa_tipo, risorsa_uid)]
    return []


def _overlap_description(
    blocked: list[tuple[str, str]], fine_effettiva, attivita_x
) -> str:
    """Build an Italian description listing all blocked resources."""
    labels = [
        f"{_TIPO_LABELS.get(tipo, tipo.capitalize())} {uid}" for tipo, uid in blocked
    ]
    if len(labels) == 1:
        resources_text = labels[0]
    elif len(labels) == 2:
        resources_text = f"{labels[0]} e {labels[1]}"
    else:
        resources_text = ", ".join(labels[:-1]) + f" e {labels[-1]}"
    return (
        f"Le risorse {resources_text} richieste per questa attivita saranno "
        f"bloccate fino al {_format_date_it(fine_effettiva)} "
        f"dall'attivita {attivita_x}."
    )


def detect_overlap_problems(attivita_x) -> list[dict]:
    """Detect resource overlaps caused by an activity's actual start date.

    When activity *X* actually starts, its effective finish
    (``actual_start + planned span``) may extend past the planned start of a
    following activity *Y* that shares one or more resources.  A single
    ``sovrapposizione`` :class:`~ui.models.ProblemaAttivita` is created on *Y*
    listing every blocked resource.

    Unresolved overlap notes previously generated by *X* are cleared first so
    that re-planning *X* (a new ``actual_start``) refreshes them; resolved
    notes are kept as history and never duplicated.

    Args:
        attivita_x: The :class:`~ui.models.Attivita` whose ``actual_start``
            was just set.  Must have ``actual_start``, ``data_inizio`` and
            ``data_fine`` populated.

    Returns:
        list[dict]: One descriptor per created overlap note with keys
            ``attivita_id``, ``activity_label``, ``problema_id``,
            ``risorse_bloccate``, ``bloccata_fino``.
    """
    from collections import defaultdict

    from django.db.models import Q
    from ui.models import ProblemaAttivita, RisorsaAttivita

    fine_effettiva = effective_finish(attivita_x)
    if fine_effettiva is None:
        return []

    pairs = list(
        RisorsaAttivita.objects.filter(attivita_id=attivita_x.pk).values_list(
            "tipo", "risorsa_id"
        )
    )
    riferimento = attivita_x.activity_id or str(attivita_x)

    # Refresh: drop stale (unresolved) overlap notes previously raised by X.
    ProblemaAttivita.objects.filter(
        tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
        riferimento_attivita=riferimento,
        risolto=False,
    ).delete()

    if not pairs:
        return []

    pair_q = Q()
    for tipo, risorsa_id in pairs:
        pair_q |= Q(tipo=tipo, risorsa_id=risorsa_id)

    conflicts = (
        RisorsaAttivita.objects.select_related("attivita")
        .filter(pair_q)
        .filter(
            attivita__data_inizio__gte=attivita_x.data_inizio,
            attivita__data_inizio__lt=fine_effettiva,
        )
        .exclude(attivita_id=attivita_x.pk)
    )

    by_successor: dict[int, list] = defaultdict(list)
    for row in conflicts:
        by_successor[row.attivita_id].append(row)

    # Skip only unresolved notes — resolved history must not block re-detection
    # after a re-import that reverts resource assignments.
    existing_sigs: set[tuple] = set(
        ProblemaAttivita.objects.filter(
            tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
            riferimento_attivita=riferimento,
            risolto=False,
        ).values_list("attivita_id", "risorsa_tipo", "risorsa_uid")
    )

    created: list[dict] = []
    for attivita_y_id, rows in by_successor.items():
        blocked = list(dict.fromkeys((r.tipo, r.risorsa_id) for r in rows))
        encoded = _encode_blocked_resources(blocked)
        sig = (attivita_y_id, "", encoded)
        if sig in existing_sigs:
            continue

        attivita_y = rows[0].attivita
        descrizione = _overlap_description(blocked, fine_effettiva, attivita_x)
        problema = ProblemaAttivita.objects.create(
            attivita=attivita_y,
            tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
            risorsa_tipo="",
            risorsa_uid=encoded,
            bloccata_fino=fine_effettiva,
            riferimento_attivita=riferimento,
            descrizione=descrizione,
        )
        created.append(
            {
                "attivita_id": attivita_y_id,
                "activity_label": str(attivita_y),
                "problema_id": problema.pk,
                "risorse_bloccate": [
                    {"tipo": tipo, "uid": uid} for tipo, uid in blocked
                ],
                "bloccata_fino": fine_effettiva.isoformat(),
            }
        )

    return created


def resolve_stale_overlaps(attivita) -> int:
    """Auto-resolve overlap notes whose conflicting resource was reassigned.

    Called after an activity's resource assignments change.  Any open
    ``sovrapposizione`` note on this activity whose ``(risorsa_tipo,
    risorsa_uid)`` is no longer assigned is marked resolved and mirrored to
    :class:`~ui.models.RisoluzioneProblema` so it survives re-imports.

    Args:
        attivita: The :class:`~ui.models.Attivita` whose resources changed.

    Returns:
        int: Number of overlap notes resolved.
    """
    from django.utils import timezone
    from ui.models import ProblemaAttivita, RisoluzioneProblema, RisorsaAttivita

    current = set(
        RisorsaAttivita.objects.filter(attivita_id=attivita.pk).values_list(
            "tipo", "risorsa_id"
        )
    )

    resolved = 0
    note = (
        "Risorsa riassegnata: la risorsa in conflitto non e piu assegnata "
        "a questa attivita."
    )
    for problema in ProblemaAttivita.objects.filter(
        attivita_id=attivita.pk,
        tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
        risolto=False,
    ):
        blocked = parse_blocked_resources(problema.risorsa_uid, problema.risorsa_tipo)
        if blocked:
            if any(pair in current for pair in blocked):
                continue
        elif (problema.risorsa_tipo, problema.risorsa_uid) in current:
            continue
        problema.risolto = True
        problema.note_risoluzione = note
        problema.data_risoluzione = timezone.now()
        problema.save(update_fields=["risolto", "note_risoluzione", "data_risoluzione"])
        RisoluzioneProblema.objects.update_or_create(
            commessa=attivita.commessa,
            item=attivita.item,
            activity=attivita.activity,
            tipo=problema.tipo,
            risorsa_tipo=problema.risorsa_tipo,
            risorsa_uid=problema.risorsa_uid,
            defaults={
                "note_risoluzione": note,
                "data_risoluzione": problema.data_risoluzione,
            },
        )
        resolved += 1

    return resolved


def _find_attivita_by_riferimento(riferimento: str):
    """Resolve the source activity referenced by an overlap note."""
    from ui.models import Attivita

    if not riferimento:
        return None
    return Attivita.objects.filter(activity_id=riferimento).first()


def reopen_stale_overlap_resolutions() -> int:
    """Re-open overlap notes that were auto-resolved while the conflict persists.

    This covers page reloads without a full re-import: a previous reassignment
    may have marked the note resolved even though the blocked resources are
    still assigned and the source activity still overlaps the successor.
    """
    from ui.models import ProblemaAttivita, RisoluzioneProblema, RisorsaAttivita

    reopened = 0
    for problema in ProblemaAttivita.objects.filter(
        tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
        risolto=True,
    ).select_related("attivita"):
        note = (problema.note_risoluzione or "").lower()
        if "lasciato invariato" in note or "accettata" in note:
            continue

        blocked = parse_blocked_resources(problema.risorsa_uid, problema.risorsa_tipo)
        if not blocked:
            continue

        attivita_y = problema.attivita
        current = set(
            RisorsaAttivita.objects.filter(attivita_id=attivita_y.pk).values_list(
                "tipo", "risorsa_id"
            )
        )
        if not any(pair in current for pair in blocked):
            continue

        attivita_x = _find_attivita_by_riferimento(problema.riferimento_attivita)
        if attivita_x is None or not attivita_y.data_inizio:
            continue

        fine_effettiva = effective_finish(attivita_x)
        if fine_effettiva is None or fine_effettiva <= attivita_y.data_inizio:
            continue

        problema.risolto = False
        problema.note_risoluzione = ""
        problema.data_risoluzione = None
        problema.save(update_fields=["risolto", "note_risoluzione", "data_risoluzione"])

        RisoluzioneProblema.objects.filter(
            commessa=attivita_y.commessa,
            item=attivita_y.item,
            activity=attivita_y.activity,
            tipo=problema.tipo,
            risorsa_tipo=problema.risorsa_tipo,
            risorsa_uid=problema.risorsa_uid,
        ).delete()
        reopened += 1

    return reopened


def refresh_overlap_warnings() -> int:
    """Ensure overlap warnings reflect the current schedule and assignments.

    Re-opens wrongly resolved overlap notes, then re-runs overlap detection for
    every activity that has an actual start date.

    Returns:
        int: Number of notes reopened plus newly created overlap notes.
    """
    from ui.models import Attivita

    total = reopen_stale_overlap_resolutions()
    for attivita in Attivita.objects.filter(actual_start__isnull=False):
        total += len(detect_overlap_problems(attivita))
    return total


def detect_for_all_activities() -> int:
    """Run problem detection for every activity in the database.

    Called after a dataset re-import so that all freshly created
    :class:`~ui.models.Attivita` rows get their problems populated.
    The underlying :func:`detect_and_save_problems` is idempotent, so
    activities that already have problems are not duplicated.

    Returns:
        int: Total number of new :class:`~ui.models.ProblemaAttivita` rows
            created across all activities.
    """
    from ui.models import Attivita

    tipo_to_plural: dict[str, str] = {
        "testa": "teste",
        "mandrino": "mandrini",
        "tastatore": "tastatori",
        "maschera": "maschere",
        "generatore": "generatori",
    }

    total = 0
    activitas = Attivita.objects.prefetch_related("risorse").all()
    for a in activitas:
        risorse_dict: dict[str, list[str]] = {}
        for r in a.risorse.all():
            plural = tipo_to_plural.get(r.tipo)
            if plural:
                risorse_dict.setdefault(plural, []).append(r.risorsa_id)
        total += detect_and_save_problems(a, risorse_dict)

    total += refresh_overlap_warnings()

    logger.info("detect_for_all_activities: %d nuovi problemi rilevati.", total)
    return total


def _should_restore_overlap_resolution(attivita, risoluzione) -> bool:
    """Return whether a saved overlap resolution should be re-applied.

    User-accepted overlaps (``lascia invariato``) are always restored.
    Auto-resolutions after resource reassignment are restored only when the
    blocked resources are no longer assigned — if they are still present the
    overlap is active again and the warning must remain visible.
    """
    from ui.models import RisorsaAttivita

    note = (risoluzione.note_risoluzione or "").lower()
    if "lasciato invariato" in note or "accettata" in note:
        return True

    blocked = parse_blocked_resources(risoluzione.risorsa_uid, risoluzione.risorsa_tipo)
    if not blocked:
        return True

    current = set(
        RisorsaAttivita.objects.filter(attivita_id=attivita.pk).values_list(
            "tipo", "risorsa_id"
        )
    )
    return not any(pair in current for pair in blocked)


def restore_resolved_notes() -> int:
    """Re-apply persistent resolution notes to freshly imported problems.

    After a dataset reset+reimport, :class:`~ui.models.ProblemaAttivita` rows
    are re-created by :func:`detect_for_all_activities`.  This function looks
    up matching :class:`~ui.models.RisoluzioneProblema` records (by activity
    identity + problem signature) and marks the corresponding problems as
    resolved, restoring the planner's notes.

    Returns:
        int: Number of problems restored as resolved.
    """
    from django.utils import timezone
    from ui.models import Attivita, ProblemaAttivita, RisoluzioneProblema

    resoluzioni = list(RisoluzioneProblema.objects.all())
    if not resoluzioni:
        return 0

    restored = 0
    for r in resoluzioni:
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

        if r.tipo == ProblemaAttivita.TIPO_SOVRAPPOSIZIONE:
            if not _should_restore_overlap_resolution(attivita, r):
                continue

        problema.risolto = True
        problema.note_risoluzione = r.note_risoluzione
        problema.data_risoluzione = r.data_risoluzione or timezone.now()
        problema.save(update_fields=["risolto", "note_risoluzione", "data_risoluzione"])
        restored += 1

    logger.info("restore_resolved_notes: %d risoluzioni ripristinate.", restored)
    return restored
