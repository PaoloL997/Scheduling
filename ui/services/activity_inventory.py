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


def get_testa_min_requirements(modello_testa: str | None) -> dict[str, int]:
    """Return minimum resource counts for a testa model configuration.

    Args:
        modello_testa: Testa model name used for configuration lookup.

    Returns:
        dict[str, int]: Keys ``teste``, ``mandrini``, ``tastatori``,
            ``maschere``, ``generatori`` with the required minimum counts.
            Defaults to ``1`` for each category when no configuration exists.
    """
    min_mandrini, min_tastatori, min_maschere, min_generatori, _ = _get_testa_config(
        modello_testa
    )
    return {
        "teste": 1,
        "mandrini": min_mandrini,
        "tastatori": min_tastatori,
        "maschere": min_maschere,
        "generatori": min_generatori,
    }


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
    strict_requirements: bool = False,
    relax_requirements: bool = False,
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
        strict_requirements: When ``True``, each category is empty unless its
            requirement fields are set (modello for teste, bore range for
            mandrini/tastatori, pitch for maschere, compatible config for
            generatori).
        relax_requirements: When ``True``, ignore model/bore/pitch filters and
            return every inventory item free in the activity interval (same-plant
            first, then other plants with ``stab_esterno``). Overrides
            ``strict_requirements``.

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

    if relax_requirements:
        strict_requirements = False

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

    def _teste_available() -> list[dict]:
        if relax_requirements:
            return _query_all_free(TestaItem.objects.all(), occ_testa)
        if strict_requirements and not modello_testa:
            return []
        return _query_with_fallback(TestaItem.objects.all(), _model_filter, occ_testa)

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
        if relax_requirements:
            return _query_all_free(GeneratoreItem.objects.all(), occ_g)
        if strict_requirements:
            if not modello_testa:
                return []
            if not gen_models:
                return []
        return _query_with_fallback(
            GeneratoreItem.objects.all(), _gen_model_filter, occ_g
        )

    teste = _teste_available()
    mandrini = _mandrini_available()
    tastatori = _tastatori_available()
    maschere = _maschere_available()
    generatori = _generatori_available()

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
        "min_requirements": get_testa_min_requirements(modello_testa),
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
