"""Inventory query helpers used by the add-activity wizard.

Provides functions that read from the Django ORM inventory models to populate
stabilimento/responsabile selectors and to list available resources (with
date-overlap exclusion) during the resource-selection step of the wizard.
"""

import math
from collections.abc import Callable
from datetime import date

#: Fixed consumable minimums of a TP60 Custom (Sporgenza Tubo-Piastra)
#: activity, replacing the testa configuration: 2 mandrini and 1 maschera,
#: no tastatore.
TP60_CUSTOM_REQUIREMENTS = {"mandrini": 2, "tastatori": 0, "maschere": 1}


def get_stabilimenti() -> list[str]:
    """Return sorted plant names from the Stabilimento ORM model.

    Returns:
        list[str]: Plant names ordered alphabetically, or ``[]`` if none exist.
    """
    from ui.models import Stabilimento

    return list(Stabilimento.objects.order_by("nome").values_list("nome", flat=True))


def get_testa_models() -> list[str]:
    """Return sorted unique testa model names from the inventory.

    Returns:
        list[str]: Distinct non-empty ``model`` values from
        :class:`~ui.models.TestaItem`, ordered alphabetically.
    """
    from ui.models import TestaItem

    return sorted(
        set(
            TestaItem.objects.exclude(model__isnull=True)
            .exclude(model="")
            .values_list("model", flat=True)
        )
    )


def get_responsabili() -> list[str]:
    """Return sorted responsible-person names from the Responsabile ORM model.

    Returns:
        list[str]: Responsible names ordered alphabetically, or ``[]`` if none exist.
    """
    from ui.models import Responsabile

    return list(Responsabile.objects.order_by("nome").values_list("nome", flat=True))


def get_tipi_attivita() -> list[str]:
    """Return sorted activity type names from the TipoAttivita ORM model.

    Returns:
        list[str]: Activity type names ordered alphabetically, or ``[]`` if none exist.
    """
    from ui.models import TipoAttivita

    return list(TipoAttivita.objects.order_by("nome").values_list("nome", flat=True))


def validate_activity_type(nome: str) -> str:
    """Validate and return the canonical activity type name from the catalog.

    Args:
        nome: Candidate activity type name from the client.

    Returns:
        str: The canonical ``nome`` stored in :class:`~ui.models.TipoAttivita`.

    Raises:
        ValueError: When *nome* is empty or does not match a configured type.
    """
    from ui.models import TipoAttivita

    cleaned = (nome or "").strip()
    if not cleaned:
        raise ValueError("Il tipo attività è obbligatorio.")

    canonical = (
        TipoAttivita.objects.filter(nome__iexact=cleaned)
        .values_list("nome", flat=True)
        .first()
    )
    if canonical is None:
        if not TipoAttivita.objects.exists():
            raise ValueError(
                "Nessun tipo attività configurato. Aggiungine almeno uno in Impostazioni."
            )
        raise ValueError(
            f"Tipo attività '{cleaned}' non valido. Seleziona un'opzione da Impostazioni."
        )
    return canonical


def _occupied_ids(
    tipo: str,
    data_inizio: date,
    data_fine: date,
    exclude_activity_id: int | None = None,
) -> set[str]:
    """Find resource IDs of a given type booked during a date range.

    Thin wrapper around
    :func:`ui.services.resource_conflicts.occupied_resource_ids` to keep
    overlap semantics in a single place.

    Args:
        tipo: Resource category (testa, mandrino, tastatore, maschera,
            generatore).
        data_inizio: Candidate activity start date.
        data_fine: Candidate activity end date.
        exclude_activity_id: Activity PK to ignore (for edit flows).

    Returns:
        set[str]: Already-occupied resource IDs.
    """
    from ui.services.resource_conflicts import occupied_resource_ids

    return occupied_resource_ids(tipo, data_inizio, data_fine, exclude_activity_id)


def _open_assignment_ids(
    tipo: str,
    exclude_activity_id: int | None = None,
) -> set[str]:
    """Return resource IDs assigned to incomplete (open) activities.

    Completed activities (both ``actual_start`` and ``actual_finish`` set)
    do not count: those resources are treated as idle for diversification.

    Args:
        tipo: Resource category (testa, mandrino, tastatore, maschera,
            generatore).
        exclude_activity_id: Activity PK to ignore (edit flows).

    Returns:
        set[str]: Resource IDs currently assigned to open activities.
    """
    from ui.models import RisorsaAttivita

    qs = RisorsaAttivita.objects.filter(tipo=tipo).exclude(
        attivita__actual_start__isnull=False,
        attivita__actual_finish__isnull=False,
    )
    if exclude_activity_id is not None:
        qs = qs.exclude(attivita_id=exclude_activity_id)
    return set(qs.values_list("risorsa_id", flat=True))


def _select_n(
    items: list[dict],
    n: int,
    busy: set[str],
    mid: float | None = None,
) -> set[str]:
    """Select N resource UIDs preferring idle items, then quality.

    Ranking:

    1. Idle (UID not in *busy*) before already-assigned.
    2. Diameter closest to *mid* when *mid* is set; otherwise original order.
    3. UID as tiebreaker.

    Args:
        items: Resource dicts with ``uid`` and optionally ``diameter``.
        n: Number of items to select.
        busy: UIDs assigned to open activities.
        mid: Optional target diameter for closest-match ranking.

    Returns:
        set[str]: Selected UIDs (fewer than *n* if the list is shorter).
    """
    if not items or n <= 0:
        return set()

    from ui.services.measures import parse_measure

    def _distance(pair: tuple[int, dict]) -> float | int:
        """Distance from *mid*; unreadable diameters sort last, never as zero."""
        if mid is None:
            return pair[0]
        diameter = parse_measure(pair[1].get("diameter"))
        return math.inf if diameter is None else abs(diameter - mid)

    ranked = sorted(
        enumerate(items),
        key=lambda pair: (
            pair[1]["uid"] in busy,
            _distance(pair),
            pair[1]["uid"],
        ),
    )
    return {item["uid"] for _, item in ranked[:n]}


def find_modello_testa_in_db(modello_testa: str) -> str | None:
    """Return the canonical DB value for a testa model name.

    Performs a two-stage lookup against :class:`~ui.models.ConfigurazioneTesta`:

    1. Case-insensitive exact match (``iexact``).
    2. If no exact match, a case-insensitive substring match (``icontains``),
       returning the first alphabetically-ordered result.

    Args:
        modello_testa: Raw testa model name extracted from the WPS filename.

    Returns:
        str | None: The canonical ``modello_testa`` string stored in the
        database, or ``None`` if no record matches.
    """
    from ui.models import ConfigurazioneTesta

    exact = (
        ConfigurazioneTesta.objects.filter(modello_testa__iexact=modello_testa)
        .values_list("modello_testa", flat=True)
        .first()
    )
    if exact is not None:
        return exact

    partial = (
        ConfigurazioneTesta.objects.filter(modello_testa__icontains=modello_testa)
        .order_by("modello_testa")
        .values_list("modello_testa", flat=True)
        .first()
    )
    return partial


def get_testa_min_requirements(
    modello_testa: str | None, tp60_custom: bool = False
) -> dict[str, int]:
    """Return minimum resource counts for a testa model configuration.

    Args:
        modello_testa: Testa model name used for configuration lookup.
        tp60_custom: When ``True`` the activity is a TP60 Custom (Sporgenza
            Tubo-Piastra) job: the consumable minimums are the fixed ones in
            :data:`TP60_CUSTOM_REQUIREMENTS` (2 mandrini, 1 maschera, no
            tastatore) instead of the configured ones.

    Returns:
        dict[str, int]: Keys ``teste``, ``mandrini``, ``tastatori``,
            ``maschere``, ``generatori`` with the required minimum counts.
            Defaults to ``1`` for each category when no configuration exists.
    """
    min_mandrini, min_tastatori, min_maschere, min_generatori, _ = _get_testa_config(
        modello_testa
    )
    requirements = {
        "teste": 1,
        "mandrini": min_mandrini,
        "tastatori": min_tastatori,
        "maschere": min_maschere,
        "generatori": min_generatori,
    }
    if tp60_custom:
        requirements.update(TP60_CUSTOM_REQUIREMENTS)
    return requirements


def _get_testa_config(modello_testa: str | None) -> tuple[int, int, int, int, set[str]]:
    """Fetch per-testa-model configuration from the ORM.

    Looks up :class:`~ui.models.ConfigurazioneTesta` for the given model name
    and returns the minimum resource counts and the set of compatible generator
    model strings.  Falls back to ``(1, 1, 1, 1, set())`` when no configuration
    exists or ``modello_testa`` is empty.

    Args:
        modello_testa: Testa model name to look up (e.g. ``"IRIS"``).

    Returns:
        tuple: ``(min_mandrini, min_tastatori, min_maschere, min_generatori,
            gen_models)`` where ``gen_models`` is a :class:`set` of compatible
            generator model strings.  Returns ``(1, 1, 1, 1, set())`` when not
            configured.
    """
    if not modello_testa:
        return 1, 1, 1, 1, set()

    from ui.models import ConfigurazioneTesta

    try:
        cfg = ConfigurazioneTesta.objects.prefetch_related(
            "generatori_compatibili"
        ).get(modello_testa=modello_testa)
    except ConfigurazioneTesta.DoesNotExist:
        return 1, 1, 1, 1, set()

    gen_models = {
        g.modello_generatore
        for g in cfg.generatori_compatibili.all()
        if g.modello_generatore
    }
    return (
        cfg.min_mandrini,
        cfg.min_tastatori,
        cfg.min_maschere,
        cfg.min_generatori,
        gen_models,
    )


#: Why a currently-assigned resource would not otherwise be listed, most
#: specific first.
_UNAVAILABLE_REASONS = (
    "manutenzione",
    "occupata",
    "non_compatibile",
    "altro_stabilimento",
)


def _unavailable_reason(
    row: dict,
    *,
    occupied: set[str],
    stabilimento: str,
    compatible_uids: set[str] | None,
) -> str | None:
    """Return why *row* is not freely selectable, or ``None`` when it is.

    Args:
        row: Inventory row dict (``uid``, ``plant``, ``in_manutenzione``, …).
        occupied: UIDs booked by another activity in the period.
        stabilimento: Plant of the activity being edited.
        compatible_uids: UIDs passing the model/bore/pitch filter, or ``None``
            when no such filter applies.

    Returns:
        str | None: One of :data:`_UNAVAILABLE_REASONS`, or ``None``.
    """
    if row.get("in_manutenzione"):
        return "manutenzione"
    if row["uid"] in occupied:
        return "occupata"
    if compatible_uids is not None and row["uid"] not in compatible_uids:
        return "non_compatibile"
    if stabilimento and (row.get("plant") or "") != stabilimento:
        return "altro_stabilimento"
    return None


def _ensure_assigned_present(
    rows: list[dict],
    model_cls,
    uids: list[str],
    *,
    occupied: set[str],
    stabilimento: str,
    compatible_uids: set[str] | None,
) -> list[dict]:
    """Return *rows* with every currently-assigned UID present and annotated.

    A resource already booked by the activity being edited must always be
    offered back, even when it fails the availability filters — otherwise the
    picker would silently drop the assignment.  Each such row is marked
    ``assigned_current`` and carries an ``unavailable_reason`` so the UI can
    explain the badge.

    Args:
        rows: Rows produced by the normal availability query.
        model_cls: Inventory model used to fetch any missing row.
        uids: UIDs currently assigned to the activity.
        occupied: UIDs booked by another activity in the period.
        stabilimento: Plant of the activity being edited.
        compatible_uids: UIDs passing the model/bore/pitch filter, or ``None``.

    Returns:
        list[dict]: Assigned rows first, then the rest in their original order.
    """
    if not uids:
        return rows

    by_uid = {r["uid"]: r for r in rows}
    missing = [u for u in uids if u and u not in by_uid]
    if missing:
        for extra in model_cls.objects.filter(uid__in=missing).order_by("uid").values():
            rows = [*rows, extra]
            by_uid[extra["uid"]] = extra

    assigned: list[dict] = []
    for uid in uids:
        row = by_uid.get(uid)
        if row is None:
            continue
        row["assigned_current"] = True
        row["unavailable_reason"] = _unavailable_reason(
            row,
            occupied=occupied,
            stabilimento=stabilimento,
            compatible_uids=compatible_uids,
        )
        if stabilimento and (row.get("plant") or "") != stabilimento:
            row.setdefault("stab_esterno", row.get("plant") or "—")
        assigned.append(row)

    assigned_uids = {r["uid"] for r in assigned}
    return assigned + [r for r in rows if r["uid"] not in assigned_uids]


def get_available_resources(
    stabilimento: str,
    data_inizio: date,
    data_fine: date,
    bore_min: float | None = None,
    bore_max: float | None = None,
    bore_mid: float | None = None,
    tube_pitch: float | None = None,
    modello_testa: str | None = None,
    exclude_activity_id: int | None = None,
    strict_requirements: bool = False,
    relax_requirements: bool = False,
    always_include: dict[str, list[str]] | None = None,
    tp60_custom: bool = False,
) -> dict:
    """Build the full resource availability context for the wizard step 5.

    Queries the ORM inventory models for each resource category, filtering by
    bore/pitch/model constraints and removing already-occupied resources.
    Resources from the activity's plant are returned when available; only when
    the plant has no matching items does the list fall back to suitable resources
    from other plants, annotated with ``stab_esterno``.

    Pre-selection is driven by the :class:`~ui.models.ConfigurazioneTesta`
    configuration for ``modello_testa`` (if one exists), then diversified
    so that resources not assigned to any open activity (in progress or
    planned) are preferred over ones already booked on other dates:

    * ``pre_mandrini`` — ``min_mandrini`` idle items with diameter closest
      to ``bore_mid`` (falls back to already-assigned if idle ones are
      insufficient).
    * ``pre_tastatori`` — same ranking as mandrini for ``min_tastatori``.
    * ``pre_maschere`` — ``min_maschere`` idle items first, then list order.

    Every count comes from ``min_requirements``, so a category whose minimum
    is ``0`` — the consumables of a TP60 Custom activity — is pre-selected
    empty.
    * ``pre_generatori`` — items whose ``model`` is in the compatible-generator
      set; the generator list itself is also filtered to those models.
      Idle items are preferred within that set.
    * ``pre_testa`` — the first idle available testa (falls back to an
      already-assigned one if none are idle).

    Completed activities (both actual dates set) do not mark a resource as
    busy.  When no configuration is found, all minimums default to 1 and
    generator filtering is not applied.

    Args:
        stabilimento: Plant name for the activity being created.
        data_inizio: Planned start date.
        data_fine: Planned end date.
        bore_min: Minimum bore diameter filter (mm).
        bore_max: Maximum bore diameter filter (mm).
        bore_mid: Mid-range bore used for closest-match pre-selection.
        tube_pitch: Required maschera pitch (mm).
        modello_testa: Testa model name used for config lookup and testa filter.
        exclude_activity_id: Activity PK to exclude from overlap checks.  When
            that activity has already started, ``data_inizio`` / ``data_fine``
            are replaced by its real occupancy window through
            :func:`ui.services.resource_conflicts.candidate_window`.
        strict_requirements: When ``True``, each category is empty unless its
            requirement fields are set (modello for teste, bore range for
            mandrini/tastatori, pitch for maschere, compatible config for
            generatori).
        relax_requirements: When ``True``, ignore model/bore/pitch filters and
            return every inventory item free in the activity interval (same-plant
            first, then other plants with ``stab_esterno``). Overrides
            ``strict_requirements``.
        always_include: Category name to list of UIDs that must appear in the
            result whatever the filters say — the resources currently assigned
            to the activity being edited.  Those rows are returned first and
            annotated with ``assigned_current`` and ``unavailable_reason``.
        tp60_custom: When ``True``, the consumable minimums are the fixed
            ones in :data:`TP60_CUSTOM_REQUIREMENTS` (2 mandrini, 1 maschera,
            no tastatore), both in ``min_requirements`` and in what gets
            pre-selected.  The lists themselves are unchanged, so the planner
            can still tick a tastatore by hand.

    Returns:
        dict: With keys ``teste``, ``mandrini``, ``tastatori``, ``maschere``,
            ``generatori`` (each a list of dicts) and ``pre_testa``,
            ``pre_mandrini``, ``pre_tastatori``, ``pre_maschere``,
            ``pre_generatori`` (each a set of pre-selected UIDs).
    """
    from ui.models import (
        GeneratoreItem,
        MandrinoItem,
        MascheraItem,
        TastatoreItem,
        TestaItem,
    )
    from ui.services.measures import is_resource_compatible

    if relax_requirements:
        strict_requirements = False

    if exclude_activity_id is not None:
        from ui.models import Attivita
        from ui.services.resource_conflicts import candidate_window

        # An activity already under way holds its resources from the day it
        # really started, so that is the interval to test — the same one the
        # save validates.  Otherwise a unit released on the planned start day
        # would look busy and drop out of the picker.
        data_inizio, data_fine = candidate_window(
            data_inizio,
            data_fine,
            Attivita.objects.filter(pk=exclude_activity_id).first(),
        )

    *_, gen_models = _get_testa_config(modello_testa)
    min_requirements = get_testa_min_requirements(modello_testa, tp60_custom)

    def _predicate(tipo: str) -> Callable[[dict], bool]:
        """Return the shared compatibility rule bound to this activity's requirements.

        Measures are compared in Python through
        :func:`ui.services.measures.is_resource_compatible` so that a
        comma-decimal or unreadable ``diameter``/``pitch`` string is handled
        the same way everywhere, rather than through a SQL cast that would
        silently truncate or zero it.

        Args:
            tipo: Resource category the predicate applies to.

        Returns:
            Callable[[dict], bool]: Row predicate.
        """

        def _pred(row: dict) -> bool:
            return is_resource_compatible(
                tipo,
                row,
                bore_min=bore_min,
                bore_max=bore_max,
                tube_pitch=tube_pitch,
                modello_testa=modello_testa,
                gen_models=gen_models,
            )

        return _pred

    occ_testa = _occupied_ids("testa", data_inizio, data_fine, exclude_activity_id)
    occ_m = _occupied_ids("mandrino", data_inizio, data_fine, exclude_activity_id)
    occ_t = _occupied_ids("tastatore", data_inizio, data_fine, exclude_activity_id)
    occ_ma = _occupied_ids("maschera", data_inizio, data_fine, exclude_activity_id)
    occ_g = _occupied_ids("generatore", data_inizio, data_fine, exclude_activity_id)

    def _query_with_fallback(
        base_qs, predicate: Callable[[dict], bool], occ: set
    ) -> list[dict]:
        """Return same-plant resources, falling back to other plants only when empty.

        Excludes occupied UIDs in SQL, applies the compatibility ``predicate``
        to the fetched rows, then returns the same-plant subset.  When no
        same-plant rows match, the other-plant rows are returned instead,
        annotated with ``stab_esterno`` so the UI can display a distinguishing
        badge.

        Args:
            base_qs: Base QuerySet for the inventory model.
            predicate: Row predicate applying bore/pitch/model filters.
            occ: Occupied UIDs to exclude.

        Returns:
            list[dict]: Same-plant rows when available; other-plant rows
                (with ``stab_esterno`` set) otherwise.
        """
        qs = base_qs
        if occ:
            qs = qs.exclude(uid__in=occ)

        primary = [
            r
            for r in qs.filter(plant=stabilimento).order_by("uid").values()
            if predicate(r)
        ]
        if primary:
            return primary

        fallback = [
            r
            for r in qs.exclude(plant=stabilimento).order_by("plant", "uid").values()
            if predicate(r)
        ]
        for r in fallback:
            r["stab_esterno"] = r.get("plant") or "—"
        return fallback

    def _query_all_free(base_qs, occ: set) -> list[dict]:
        """Return all free resources in the period, same-plant first.

        Unlike ``_query_with_fallback``, other-plant items are always appended
        (with ``stab_esterno``) rather than only when the local plant is empty.
        """
        qs = base_qs
        if occ:
            qs = qs.exclude(uid__in=occ)
        primary = list(qs.filter(plant=stabilimento).order_by("uid").values())
        others = list(qs.exclude(plant=stabilimento).order_by("plant", "uid").values())
        for r in others:
            r["stab_esterno"] = r.get("plant") or "—"
        return primary + others

    _bore_filter = _predicate("mandrino")
    _pitch_filter = _predicate("maschera")
    _model_filter = _predicate("testa")
    _gen_model_filter = _predicate("generatore")

    def _teste_available() -> list[dict]:
        base = TestaItem.objects.filter(in_manutenzione=False)
        if relax_requirements:
            return _query_all_free(base, occ_testa)
        if strict_requirements and not modello_testa:
            return []
        return _query_with_fallback(base, _model_filter, occ_testa)

    def _mandrini_available() -> list[dict]:
        if relax_requirements:
            return _query_all_free(MandrinoItem.objects.all(), occ_m)
        if strict_requirements and (bore_min is None or bore_max is None):
            return []
        return _query_with_fallback(MandrinoItem.objects.all(), _bore_filter, occ_m)

    def _tastatori_available() -> list[dict]:
        if relax_requirements:
            return _query_all_free(TastatoreItem.objects.all(), occ_t)
        if strict_requirements and (bore_min is None or bore_max is None):
            return []
        return _query_with_fallback(TastatoreItem.objects.all(), _bore_filter, occ_t)

    def _maschere_available() -> list[dict]:
        if relax_requirements:
            return _query_all_free(MascheraItem.objects.all(), occ_ma)
        if strict_requirements and tube_pitch is None:
            return []
        return _query_with_fallback(MascheraItem.objects.all(), _pitch_filter, occ_ma)

    def _generatori_available() -> list[dict]:
        base = GeneratoreItem.objects.filter(in_manutenzione=False)
        if relax_requirements:
            return _query_all_free(base, occ_g)
        if strict_requirements:
            if not modello_testa:
                return []
            if not gen_models:
                return []
        return _query_with_fallback(base, _gen_model_filter, occ_g)

    teste = _teste_available()
    mandrini = _mandrini_available()
    tastatori = _tastatori_available()
    maschere = _maschere_available()
    generatori = _generatori_available()

    if always_include:

        def _compatible(model_cls, predicate, active: bool) -> set[str] | None:
            """UIDs passing the model/bore/pitch filter, or None when unfiltered."""
            if not active:
                return None
            return {r["uid"] for r in model_cls.objects.values() if predicate(r)}

        has_bore = bore_min is not None and bore_max is not None
        specs = (
            ("teste", TestaItem, _model_filter, occ_testa, bool(modello_testa)),
            ("mandrini", MandrinoItem, _bore_filter, occ_m, has_bore),
            ("tastatori", TastatoreItem, _bore_filter, occ_t, has_bore),
            ("maschere", MascheraItem, _pitch_filter, occ_ma, tube_pitch is not None),
            ("generatori", GeneratoreItem, _gen_model_filter, occ_g, bool(gen_models)),
        )
        lists = {
            "teste": teste,
            "mandrini": mandrini,
            "tastatori": tastatori,
            "maschere": maschere,
            "generatori": generatori,
        }
        for key, model_cls, filter_fn, occ, filter_active in specs:
            uids = [u for u in (always_include.get(key) or []) if u]
            if not uids:
                continue
            lists[key] = _ensure_assigned_present(
                lists[key],
                model_cls,
                uids,
                occupied=occ,
                stabilimento=stabilimento,
                compatible_uids=_compatible(model_cls, filter_fn, filter_active),
            )
        teste = lists["teste"]
        mandrini = lists["mandrini"]
        tastatori = lists["tastatori"]
        maschere = lists["maschere"]
        generatori = lists["generatori"]

    busy_testa = _open_assignment_ids("testa", exclude_activity_id)
    busy_m = _open_assignment_ids("mandrino", exclude_activity_id)
    busy_t = _open_assignment_ids("tastatore", exclude_activity_id)
    busy_ma = _open_assignment_ids("maschera", exclude_activity_id)
    busy_g = _open_assignment_ids("generatore", exclude_activity_id)

    # Pre-selection follows the same minimums the wizard enforces, so a TP60
    # Custom activity (consumable minimums at 0) starts with no mandrini,
    # tastatori or maschere ticked.
    pre_testa = _select_n(teste, min_requirements["teste"], busy_testa)
    pre_mandrini = _select_n(mandrini, min_requirements["mandrini"], busy_m, bore_mid)
    pre_tastatori = _select_n(
        tastatori, min_requirements["tastatori"], busy_t, bore_mid
    )
    pre_maschere = _select_n(maschere, min_requirements["maschere"], busy_ma)

    if gen_models:
        compatible = [r for r in generatori if r.get("model") in gen_models]
        pre_generatori = _select_n(compatible, min_requirements["generatori"], busy_g)
    else:
        pre_generatori = _select_n(generatori, min_requirements["generatori"], busy_g)

    def _pre_first(lst: list, pre: set) -> list:
        """Place pre-selected items at the front of a list.

        Args:
            lst: Full resource list.
            pre: Set of pre-selected UIDs.

        Returns:
            list: Pre-selected items first, remaining items after.
        """
        keep = [x for x in lst if x.get("assigned_current")]
        rest = [x for x in lst if not x.get("assigned_current")]
        return (
            keep
            + [x for x in rest if x["uid"] in pre]
            + [x for x in rest if x["uid"] not in pre]
        )

    return {
        "teste": _pre_first(teste, pre_testa),
        "mandrini": _pre_first(mandrini, pre_mandrini),
        "tastatori": _pre_first(tastatori, pre_tastatori),
        "maschere": _pre_first(maschere, pre_maschere),
        "generatori": _pre_first(generatori, pre_generatori),
        "pre_testa": pre_testa,
        "pre_mandrini": pre_mandrini,
        "pre_tastatori": pre_tastatori,
        "pre_maschere": pre_maschere,
        "pre_generatori": pre_generatori,
        "min_requirements": min_requirements,
    }


def lookup_resources(pairs: list[tuple[str, str]]) -> dict[str, dict]:
    """Return inventory metadata for specific resource UIDs.

    Args:
        pairs: ``(categoria, uid)`` tuples where *categoria* is one of
            ``testa``, ``mandrino``, ``tastatore``, ``maschera``,
            ``generatore``.

    Returns:
        dict[str, dict]: Mapping ``uid`` → row dict from the inventory ORM.
    """
    from ui.models import (
        GeneratoreItem,
        MandrinoItem,
        MascheraItem,
        TastatoreItem,
        TestaItem,
    )

    models = {
        "testa": TestaItem,
        "mandrino": MandrinoItem,
        "tastatore": TastatoreItem,
        "maschera": MascheraItem,
        "generatore": GeneratoreItem,
    }
    out: dict[str, dict] = {}
    for categoria, uid in pairs:
        uid = (uid or "").strip()
        if not uid or uid in out:
            continue
        model = models.get((categoria or "").strip().lower())
        if model is None:
            continue
        row = model.objects.filter(uid=uid).values().first()
        if row:
            out[uid] = dict(row)
    return out


def parse_resource_lookup_param(raw: str) -> list[tuple[str, str]]:
    """Parse ``tipo:uid`` pairs from a comma-separated lookup string."""
    pairs: list[tuple[str, str]] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        tipo, uid = part.split(":", 1)
        tipo = tipo.strip().lower()
        uid = uid.strip()
        if tipo and uid:
            pairs.append((tipo, uid))
    return pairs
