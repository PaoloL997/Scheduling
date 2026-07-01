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

    logger.info("detect_for_all_activities: %d nuovi problemi rilevati.", total)
    return total


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

        problema.risolto = True
        problema.note_risoluzione = r.note_risoluzione
        problema.data_risoluzione = r.data_risoluzione or timezone.now()
        problema.save(update_fields=["risolto", "note_risoluzione", "data_risoluzione"])
        restored += 1

    logger.info("restore_resolved_notes: %d risoluzioni ripristinate.", restored)
    return restored
