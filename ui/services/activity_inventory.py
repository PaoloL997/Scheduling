"""Inventory query helpers used by the add-activity wizard.

Provides functions that read from the Django ORM inventory models to populate
stabilimento/responsabile selectors and to list available resources (with
date-overlap exclusion) during the resource-selection step of the wizard.
"""

from datetime import date


def get_stabilimenti() -> list[str]:
    """Return sorted plant names from the Stabilimento ORM model.

    Returns:
        list[str]: Plant names ordered alphabetically, or ``[]`` if none exist.
    """
    from ui.models import Stabilimento

    return list(Stabilimento.objects.order_by("nome").values_list("nome", flat=True))


def get_responsabili() -> list[str]:
    """Return sorted responsible-person names from the Responsabile ORM model.

    Returns:
        list[str]: Responsible names ordered alphabetically, or ``[]`` if none exist.
    """
    from ui.models import Responsabile

    return list(Responsabile.objects.order_by("nome").values_list("nome", flat=True))


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
) -> dict:
    """Build the full resource availability context for the wizard step 5.

    Queries the ORM inventory models for each resource category, filtering by
    bore/pitch/model constraints and removing already-occupied resources.
    Resources from the activity's plant are returned when available; only when
    the plant has no matching items does the list fall back to suitable resources
    from other plants, annotated with ``stab_esterno``.

    Pre-selection is driven by the :class:`~ui.models.ConfigurazioneTesta`
    configuration for ``modello_testa`` (if one exists):

    * ``pre_mandrini`` — the ``min_mandrini`` items with diameter closest to
      ``bore_mid``.
    * ``pre_tastatori`` — the ``min_tastatori`` items with diameter closest to
      ``bore_mid``.
    * ``pre_maschere`` — the first ``min_maschere`` items in the list.
    * ``pre_generatori`` — items whose ``model`` is in the compatible-generator
      set; the generator list itself is also filtered to those models.
    * ``pre_testa`` — the first available testa (unchanged).

    When no configuration is found, all minimums default to 1 and generator
    filtering is not applied.

    Args:
        stabilimento: Plant name for the activity being created.
        data_inizio: Planned start date.
        data_fine: Planned end date.
        bore_min: Minimum bore diameter filter (mm).
        bore_max: Maximum bore diameter filter (mm).
        bore_mid: Mid-range bore used for closest-match pre-selection.
        tube_pitch: Required maschera pitch (mm).
        modello_testa: Testa model name used for config lookup and testa filter.
        exclude_activity_id: Activity PK to exclude from overlap checks.

    Returns:
        dict: With keys ``teste``, ``mandrini``, ``tastatori``, ``maschere``,
            ``generatori`` (each a list of dicts) and ``pre_testa``,
            ``pre_mandrini``, ``pre_tastatori``, ``pre_maschere``,
            ``pre_generatori`` (each a set of pre-selected UIDs).
    """
    from django.db.models import FloatField
    from django.db.models.functions import Cast
    from ui.models import (
        GeneratoreItem,
        MandrinoItem,
        MascheraItem,
        TastatoreItem,
        TestaItem,
    )

    min_mandrini, min_tastatori, min_maschere, min_generatori, gen_models = (
        _get_testa_config(modello_testa)
    )

    occ_testa = _occupied_ids("testa", data_inizio, data_fine, exclude_activity_id)
    occ_m = _occupied_ids("mandrino", data_inizio, data_fine, exclude_activity_id)
    occ_t = _occupied_ids("tastatore", data_inizio, data_fine, exclude_activity_id)
    occ_ma = _occupied_ids("maschera", data_inizio, data_fine, exclude_activity_id)
    occ_g = _occupied_ids("generatore", data_inizio, data_fine, exclude_activity_id)

    def _query_with_fallback(base_qs, extra_filter_fn, occ: set) -> list[dict]:
        """Return same-plant resources, falling back to other plants only when empty.

        Applies ``extra_filter_fn`` and excludes occupied UIDs, then returns the
        same-plant subset.  When no same-plant rows match, the other-plant rows
        are returned instead, annotated with ``stab_esterno`` so the UI can
        display a distinguishing badge.

        Args:
            base_qs: Base QuerySet for the inventory model.
            extra_filter_fn: Callable that applies bore/pitch/model filters.
            occ: Occupied UIDs to exclude.

        Returns:
            list[dict]: Same-plant rows when available; other-plant rows
                (with ``stab_esterno`` set) otherwise.
        """
        qs = extra_filter_fn(base_qs)
        if occ:
            qs = qs.exclude(uid__in=occ)

        primary = list(qs.filter(plant=stabilimento).order_by("uid").values())
        if primary:
            return primary

        fallback = list(
            qs.exclude(plant=stabilimento).order_by("plant", "uid").values()
        )
        for r in fallback:
            r["stab_esterno"] = r.get("plant") or "—"
        return fallback

    def _bore_filter(qs):
        """Filter queryset by bore diameter range when both bounds are set.

        Args:
            qs: QuerySet with a ``diameter`` CharField.

        Returns:
            QuerySet: Filtered or unmodified queryset.
        """
        if bore_min is not None and bore_max is not None:
            return qs.annotate(_d=Cast("diameter", FloatField())).filter(
                _d__gte=bore_min, _d__lte=bore_max
            )
        return qs

    def _pitch_filter(qs):
        """Filter queryset to rows matching the required tube pitch.

        Args:
            qs: QuerySet with a ``pitch`` CharField.

        Returns:
            QuerySet: Filtered or unmodified queryset.
        """
        if tube_pitch is not None:
            return qs.annotate(_p=Cast("pitch", FloatField())).filter(_p=tube_pitch)
        return qs

    def _model_filter(qs):
        """Filter queryset to rows matching the testa model.

        Args:
            qs: QuerySet with a ``model`` CharField.

        Returns:
            QuerySet: Filtered or unmodified queryset.
        """
        if modello_testa:
            return qs.filter(model=modello_testa)
        return qs

    def _gen_model_filter(qs):
        """Filter generatori to compatible models when configuration exists.

        When ``gen_models`` is non-empty (i.e. a :class:`ConfigurazioneTesta`
        with compatible generators was found), only those generator items whose
        ``model`` appears in the compatible set are returned.

        Args:
            qs: QuerySet for :class:`~ui.models.GeneratoreItem`.

        Returns:
            QuerySet: Filtered or unmodified queryset.
        """
        if gen_models:
            return qs.filter(model__in=gen_models)
        return qs

    teste = _query_with_fallback(TestaItem.objects.all(), _model_filter, occ_testa)
    mandrini = _query_with_fallback(MandrinoItem.objects.all(), _bore_filter, occ_m)
    tastatori = _query_with_fallback(TastatoreItem.objects.all(), _bore_filter, occ_t)
    maschere = _query_with_fallback(MascheraItem.objects.all(), _pitch_filter, occ_ma)
    generatori = _query_with_fallback(
        GeneratoreItem.objects.all(), _gen_model_filter, occ_g
    )

    pre_testa: set = {teste[0]["uid"]} if teste else set()

    def _closest_n(items: list[dict], n: int, mid: float | None) -> set[str]:
        """Select UIDs of the N items with diameter closest to a target value.

        When ``mid`` is ``None``, returns the UIDs of the first ``n`` items.

        Args:
            items: Resource dicts, each expected to have ``uid`` and
                ``diameter`` keys.
            n: Number of items to select.
            mid: Target diameter for closest-match ranking.

        Returns:
            set[str]: The selected UIDs (fewer than ``n`` if list is shorter).
        """
        if not items:
            return set()
        if mid is not None:
            ranked = sorted(
                items,
                key=lambda x: abs(float(x.get("diameter") or 0) - mid),
            )
        else:
            ranked = items
        return {r["uid"] for r in ranked[:n]}

    def _first_n(items: list[dict], n: int) -> set[str]:
        """Return the UIDs of the first ``n`` items in a list.

        Args:
            items: Resource dicts, each expected to have a ``uid`` key.
            n: Number of items to select.

        Returns:
            set[str]: The selected UIDs (fewer than ``n`` if list is shorter).
        """
        return {r["uid"] for r in items[:n]}

    pre_mandrini = _closest_n(mandrini, min_mandrini, bore_mid)
    pre_tastatori = _closest_n(tastatori, min_tastatori, bore_mid)
    pre_maschere = _first_n(maschere, min_maschere)

    if gen_models:
        compatible = [r for r in generatori if r.get("model") in gen_models]
        pre_generatori: set = _first_n(compatible, min_generatori)
    else:
        pre_generatori = {generatori[0]["uid"]} if generatori else set()

    def _pre_first(lst: list, pre: set) -> list:
        """Place pre-selected items at the front of a list.

        Args:
            lst: Full resource list.
            pre: Set of pre-selected UIDs.

        Returns:
            list: Pre-selected items first, remaining items after.
        """
        return [x for x in lst if x["uid"] in pre] + [
            x for x in lst if x["uid"] not in pre
        ]

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
    }
