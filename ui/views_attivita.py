"""AJAX API endpoints for the add-activity multi-step wizard.

All state is accumulated client-side in JavaScript and submitted in a single
final POST to :func:`api_salva_attivita`.  The endpoints are called from the
modal wizard embedded in the ``pianificazione`` page.
"""

import logging
import os
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ui.models import Attivita, RisorsaAttivita
from ui.permissions import write_required
from ui.services.activity_inventory import (
    find_modello_testa_in_db,
    get_available_resources,
    lookup_resources,
    parse_resource_lookup_param,
)
from ui.services.measures import parse_measure
from ui.services.parsing import parse_day_any_format, parse_decimal, parse_json_body
from ui.services.resource_types import risorse_dict_to_pairs
from ui.services.identifiers import build_job_id
from ui.services.wps_ai import ReadWPS
from ui.services.wps_parser import compute_bore, extract_tube_pitch, parse_wps_name

logger = logging.getLogger(__name__)


# ── AJAX: analizza WPS ────────────────────────────────────────────────────────


@write_required
@csrf_exempt
@require_http_methods(["POST"])
def api_analizza_wps(request: HttpRequest) -> JsonResponse:
    """Upload a WPS PDF, parse its filename, and run the AI analysis.

    Saves the file to ``MEDIA_ROOT/wps/`` under a temporary name.  Even if
    the analysis fails, the endpoint returns success with the filename-parsed
    fields and an empty ``bore`` dict so the wizard can continue.

    The ``modello_testa`` in ``parsed`` is normalised to the canonical value
    stored in :class:`~ui.models.ConfigurazioneTesta` when a match is found
    (case-insensitive exact first, substring fallback).  The ``testa_db_match``
    flag in ``parsed`` indicates whether a matching configuration record exists.

    Args:
        request: Multipart POST with a ``wps_file`` field.

    Returns:
        JsonResponse with ``ok=True`` and keys ``tmp_path``, ``wps_name``,
        ``parsed``, ``misure``, ``bore`` on success, or ``ok=False`` on
        validation error.  ``parsed`` includes ``testa_db_match`` (bool).
    """
    pdf = request.FILES.get("wps_file")
    if not pdf:
        return JsonResponse({"ok": False, "error": "Nessun file allegato."}, status=400)
    if not pdf.name.lower().endswith(".pdf"):
        return JsonResponse(
            {"ok": False, "error": "Il file deve essere in formato PDF."}, status=400
        )

    parsed = parse_wps_name(pdf.name)
    if not parsed.get("modello_testa") or not parsed.get("commessa"):
        return JsonResponse(
            {
                "ok": False,
                "error": "Nome file non valido. Usa il formato "
                "ModelloTesta_Commessa[_Item].pdf.",
            },
            status=400,
        )

    matched_modello = find_modello_testa_in_db(parsed["modello_testa"])
    if matched_modello is not None:
        parsed["modello_testa"] = matched_modello
    parsed["testa_db_match"] = matched_modello is not None

    tmp_path = default_storage.save(f"wps/_tmp_{pdf.name}", pdf)
    abs_path = os.path.join(settings.MEDIA_ROOT, tmp_path)

    logger.info(
        "WPS analizza start: file=%s tmp_path=%s parsed=%s user=%s",
        pdf.name,
        tmp_path,
        parsed,
        getattr(request.user, "username", "?"),
    )

    misure = ReadWPS().invoke(abs_path)
    bore = compute_bore(misure.get("diameter"), misure.get("s1"))
    if not isinstance(bore, dict):
        bore = {}
    bore["tube_pitch"] = extract_tube_pitch(misure.get("tube_pitch"))

    logger.info(
        "WPS analizza result: file=%s misure=%s bore=%s",
        pdf.name,
        misure,
        bore,
    )

    return JsonResponse(
        {
            "ok": True,
            "tmp_path": tmp_path,
            "wps_name": pdf.name,
            "parsed": parsed,
            "misure": misure,
            "bore": bore,
        }
    )


# ── AJAX: risorse disponibili ─────────────────────────────────────────────────


def _assigned_uids_by_cat(attivita_id: int) -> dict[str, list[str]]:
    """Return the resources already booked by an activity, keyed by category.

    Feeds ``always_include`` so the edit picker can never drop an existing
    assignment just because it fails the availability filters.

    Args:
        attivita_id: Primary key of the activity being edited.

    Returns:
        dict[str, list[str]]: Wizard category name to list of UIDs.
    """
    from ui.models import RisorsaAttivita
    from ui.services.problem_detection import _TIPO_TO_CAT

    out: dict[str, list[str]] = {}
    rows = RisorsaAttivita.objects.filter(attivita_id=attivita_id).values_list(
        "tipo", "risorsa_id"
    )
    for tipo, uid in rows:
        cat = _TIPO_TO_CAT.get(tipo)
        if cat and uid:
            out.setdefault(cat, []).append(uid)
    return out


@login_required
def api_get_risorse(request: HttpRequest) -> JsonResponse:
    """Return available resources for the candidate activity.

    Query parameters: ``stabilimento``, ``data_inizio``, ``data_fine``,
    ``bore_min``, ``bore_max``, ``bore_mid``, ``tube_pitch``,
    ``modello_testa``, ``tp60_custom``.

    When ``exclude_activity_id`` is given, ``tp60_custom`` defaults to the
    flag stored on that activity.

    Args:
        request: GET request with filter parameters.

    Returns:
        JsonResponse with resource lists and pre-selection sets for all
        five categories.
    """
    stabilimento = request.GET.get("stabilimento", "")
    try:
        data_inizio = date.fromisoformat(request.GET.get("data_inizio", ""))
        data_fine = date.fromisoformat(request.GET.get("data_fine", ""))
    except ValueError:
        return JsonResponse({"ok": False, "error": "Date non valide."}, status=400)

    from ui.services.measures import parse_measure as _float

    always_include: dict[str, list[str]] | None = None
    exclude_activity_id = None
    raw_exclude = request.GET.get("exclude_activity_id", "").strip()
    if raw_exclude:
        try:
            exclude_activity_id = int(raw_exclude)
        except (ValueError, TypeError):
            return JsonResponse(
                {"ok": False, "error": "exclude_activity_id non valido."},
                status=400,
            )

    bore_min = _float(request.GET.get("bore_min"))
    bore_max = _float(request.GET.get("bore_max"))
    bore_mid = _float(request.GET.get("bore_mid"))
    tube_pitch = _float(request.GET.get("tube_pitch"))
    modello_testa = (request.GET.get("modello_testa") or "").strip() or None

    strict_requirements = request.GET.get("strict_requirements", "").lower() in (
        "1",
        "true",
        "yes",
    )
    relax_requirements = request.GET.get("relax_requirements", "").lower() in (
        "1",
        "true",
        "yes",
    )
    raw_tp60_custom = request.GET.get("tp60_custom", "").strip().lower()
    tp60_custom = raw_tp60_custom in ("1", "true", "yes")

    if exclude_activity_id:
        try:
            att = Attivita.objects.get(pk=exclude_activity_id)
        except Attivita.DoesNotExist:
            return JsonResponse(
                {"ok": False, "error": "Attività non trovata."},
                status=404,
            )
        if not modello_testa and att.modello_testa:
            modello_testa = att.modello_testa
        if bore_min is None and att.bore_min is not None:
            bore_min = float(att.bore_min)
        if bore_max is None and att.bore_max is not None:
            bore_max = float(att.bore_max)
        if bore_mid is None and att.bore_mid is not None:
            bore_mid = float(att.bore_mid)
        if tube_pitch is None and att.tube_pitch is not None:
            tube_pitch = float(att.tube_pitch)
        if not stabilimento and att.stabilimento:
            stabilimento = att.stabilimento
        if not raw_tp60_custom:
            tp60_custom = bool(att.tp60_custom)
        # Strict filtering empties a category when its requirement is unset,
        # which would leave the edit picker with nothing to choose from.
        if not relax_requirements and modello_testa:
            strict_requirements = True
        always_include = _assigned_uids_by_cat(exclude_activity_id)

    result = get_available_resources(
        stabilimento=stabilimento,
        data_inizio=data_inizio,
        data_fine=data_fine,
        bore_min=bore_min,
        bore_max=bore_max,
        bore_mid=bore_mid,
        tube_pitch=tube_pitch,
        modello_testa=modello_testa,
        exclude_activity_id=exclude_activity_id,
        strict_requirements=strict_requirements,
        relax_requirements=relax_requirements,
        always_include=always_include,
        tp60_custom=tp60_custom,
    )

    lookup_pairs = parse_resource_lookup_param(request.GET.get("lookup", ""))
    lookup = lookup_resources(lookup_pairs) if lookup_pairs else {}

    return JsonResponse(
        {
            "ok": True,
            "teste": result["teste"],
            "mandrini": result["mandrini"],
            "tastatori": result["tastatori"],
            "maschere": result["maschere"],
            "generatori": result["generatori"],
            "pre_testa": list(result["pre_testa"]),
            "pre_mandrini": list(result["pre_mandrini"]),
            "pre_tastatori": list(result["pre_tastatori"]),
            "pre_maschere": list(result["pre_maschere"]),
            "pre_generatori": list(result["pre_generatori"]),
            "min_requirements": result["min_requirements"],
            "lookup": lookup,
        }
    )


# ── AJAX: salva attività ──────────────────────────────────────────────────────


@write_required
@csrf_exempt
@require_http_methods(["POST"])
def api_salva_attivita(request: HttpRequest) -> JsonResponse:
    """Persist the completed activity and its resource assignments.

    Expects a JSON body with all wizard fields collected client-side.
    Creates :class:`~ui.models.Attivita` and
    :class:`~ui.models.RisorsaAttivita` rows atomically.  If ``tmp_path``
    is provided the temporary WPS file is moved to its final location.

    Args:
        request: POST with a JSON body containing the wizard state.

    Returns:
        JsonResponse ``{"ok": True, "id": <pk>}`` on success or
        ``{"ok": False, "error": "..."}`` on validation/save failure.
    """
    data = parse_json_body(request)
    if isinstance(data, JsonResponse):
        return data

    tmp_path = data.get("tmp_path", "")
    if tmp_path and not default_storage.exists(tmp_path):
        return JsonResponse(
            {"ok": False, "error": "File temporaneo non trovato. Ricarica il WPS."},
            status=400,
        )

    commessa = data.get("commessa", "").strip()
    if not commessa:
        return JsonResponse(
            {"ok": False, "error": "Commessa obbligatoria."}, status=400
        )

    item = str(data.get("item", "")).strip() or "1"

    from ui.services.activity_inventory import validate_activity_type

    try:
        attivita = validate_activity_type(data.get("attivita", ""))
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    try:
        data_inizio = date.fromisoformat(data["data_inizio"])
        data_fine = date.fromisoformat(data["data_fine"])
    except (KeyError, ValueError):
        return JsonResponse({"ok": False, "error": "Date non valide."}, status=400)
    if data_fine < data_inizio:
        return JsonResponse(
            {"ok": False, "error": "La fine prevista non puo' precedere l'inizio."},
            status=400,
        )

    bore = data.get("bore", {})

    from ui.services.precedences import resolve_predecessors

    try:
        predecessor_objs = resolve_predecessors(commessa, data.get("predecessors", []))
    except ValueError:
        return JsonResponse(
            {
                "ok": False,
                "error": "Predecessor non validi per la commessa selezionata.",
            },
            status=400,
        )

    from ui.models import Job

    def _delivery_iso(raw) -> str | None:
        parsed = parse_day_any_format(raw)
        return parsed.isoformat() if parsed else None

    job_exists = Job.objects.filter(commessa=commessa).exists()
    new_job = data.get("new_job")
    if not job_exists:
        if not new_job:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Per una nuova commessa devi indicare data di consegna.",
                },
                status=400,
            )
        delivery_date = _delivery_iso(new_job.get("delivery_date"))
        if not delivery_date:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Per una nuova commessa devi indicare una data di consegna valida.",
                },
                status=400,
            )
        new_job = {**new_job, "delivery_date": delivery_date}
        raw_cost = new_job.get("total_cost")
        if raw_cost not in (None, "") and parse_measure(raw_cost) is None:
            return JsonResponse(
                {"ok": False, "error": "Costo totale non valido."}, status=400
            )
        raw_penalty = new_job.get("penalty")
        if raw_penalty not in (None, "") and parse_measure(raw_penalty) is None:
            return JsonResponse(
                {"ok": False, "error": "Penale non valida."}, status=400
            )

    risorse_data = data.get("risorse", {})
    assignments_pairs = risorse_dict_to_pairs(risorse_data)

    from ui.services.resource_conflicts import (
        ResourceConflictError,
        format_conflict_message,
        validate_no_conflicts,
    )

    try:
        validate_no_conflicts(assignments_pairs, data_inizio, data_fine)
    except ResourceConflictError as exc:
        return JsonResponse(
            {
                "ok": False,
                "error": format_conflict_message(
                    exc.conflicts,
                    action_hint=(
                        "Torna allo step risorse per scegliere alternative "
                        "non occupate nel periodo indicato."
                    ),
                ),
                "conflicts": exc.conflicts,
            },
            status=409,
        )

    with transaction.atomic():
        att = Attivita(
            commessa=commessa,
            item=item,
            activity=attivita,
            stabilimento=data.get("stabilimento", ""),
            responsabile=data.get("responsabile", ""),
            modello_testa=data.get("modello_testa", ""),
            data_inizio=data_inizio,
            data_fine=data_fine,
            bore_min=parse_decimal(bore.get("min")),
            bore_max=parse_decimal(bore.get("max")),
            bore_mid=parse_decimal(bore.get("mid")),
            tube_pitch=parse_decimal(bore.get("tube_pitch")),
            tp60_custom=bool(data.get("tp60_custom")),
        )

        if tmp_path:
            default_storage.delete(tmp_path)

        # The Job row comes first, with its metadata: saving the activity
        # links it to the Job of its commessa (creating a bare one otherwise).
        if new_job and new_job.get("commessa"):
            job_commessa = new_job["commessa"].strip()
            if job_commessa:
                Job.objects.get_or_create(
                    commessa=job_commessa,
                    defaults={
                        "job_id": build_job_id(job_commessa),
                        "total_cost": parse_measure(new_job.get("total_cost")),
                        "penalty": parse_measure(new_job.get("penalty")),
                        "delivery_date": new_job.get("delivery_date") or None,
                    },
                )

        att.save()
        if predecessor_objs:
            att.predecessori.set(predecessor_objs)

        risorsa_rows = [
            RisorsaAttivita(attivita=att, tipo=tipo, risorsa_id=rid)
            for tipo, rid in assignments_pairs
        ]
        if risorsa_rows:
            RisorsaAttivita.objects.bulk_create(risorsa_rows)

    # Reconcile the notes now that the rows exist: acquisto/spostamento for
    # the new activity and, when it is planned in the past, its ritardo note.
    from ui.services.reconcile import reconcile

    reconcile([att.pk])

    from ui.services.operation_log import record_operation

    record_operation(
        request,
        azione="attivita_crea",
        descrizione=f"Creata attività {att.commessa}/{att.item}/{att.activity}",
        riferimento=att.activity_id,
    )

    from ui.services.planning_read import job_summary, load_task_dict

    task = load_task_dict(att.pk)
    return JsonResponse(
        {
            "ok": True,
            "id": att.pk,
            "warning_count": task["warning_count"] if task else 0,
            "note_count": task["note_count"] if task else 0,
            "task": task,
            "job": job_summary(att.commessa),
        }
    )


# ── AJAX: heatmap data ────────────────────────────────────────────────────────


@login_required
def attivita_heatmap_data(request: HttpRequest) -> JsonResponse:
    """Return daily activity counts for the planning calendar heatmap.

    Args:
        request: GET with ``start`` and ``end`` ISO-date query parameters.

    Returns:
        JsonResponse ``{"counts": {"YYYY-MM-DD": N, ...}}``.
    """
    try:
        from_date = date.fromisoformat(request.GET.get("start", ""))
        to_date = date.fromisoformat(request.GET.get("end", ""))
    except ValueError:
        return JsonResponse({"error": "date non valide"}, status=400)

    if (to_date - from_date).days > 730:
        return JsonResponse({"error": "intervallo troppo grande"}, status=400)

    activities = list(
        Attivita.objects.filter(
            data_inizio__isnull=False,
            data_fine__isnull=False,
            data_inizio__lte=to_date,
            data_fine__gte=from_date,
        ).values_list("data_inizio", "data_fine")
    )

    counts: dict[str, int] = {}
    current = from_date
    while current <= to_date:
        c = sum(1 for d0, d1 in activities if d0 <= current <= d1)
        if c:
            counts[current.isoformat()] = c
        current += timedelta(days=1)

    return JsonResponse({"counts": counts})
