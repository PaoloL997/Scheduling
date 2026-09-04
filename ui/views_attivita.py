"""AJAX API endpoints for the add-activity multi-step wizard.

All state is accumulated client-side in JavaScript and submitted in a single
final POST to :func:`api_salva_attivita`.  The endpoints are called from the
modal wizard embedded in the ``pianificazione`` page.
"""

import json
import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ui.models import Attivita, MppSyncQueue, RisorsaAttivita
from ui.permissions import write_required
from ui.services.activity_inventory import (
    find_modello_testa_in_db,
    get_available_resources,
    lookup_resources,
    parse_resource_lookup_param,
)
from ui.services.gemini import ReadWPS
from ui.services.identifiers import build_job_id
from ui.services.wps_parser import compute_bore, extract_tube_pitch, parse_wps_name

logger = logging.getLogger(__name__)


# ── AJAX: analizza WPS ────────────────────────────────────────────────────────


@write_required
@csrf_exempt
@require_http_methods(["POST"])
def api_analizza_wps(request: HttpRequest) -> JsonResponse:
    """Upload a WPS PDF, parse its filename, and run Gemini analysis.

    Saves the file to ``MEDIA_ROOT/wps/`` under a temporary name.  Even if
    Gemini fails, the endpoint returns success with the filename-parsed fields
    and an empty ``bore`` dict so the wizard can continue.

    The ``modello_testa`` in ``parsed`` is normalised to the canonical value
    stored in :class:`~ui.models.ConfigurazioneTesta` when a match is found
    (case-insensitive exact first, substring fallback).  The ``testa_db_match``
    flag in ``parsed`` indicates whether a matching configuration record exists.

    Args:
        request: Multipart POST with a ``wps_file`` field.

    Returns:
        JsonResponse with ``ok=True`` and keys ``tmp_path``, ``wps_name``,
        ``parsed``, ``gemini``, ``bore`` on success, or ``ok=False`` on
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

    gemini_data = ReadWPS().invoke(abs_path)
    bore = compute_bore(gemini_data.get("diameter"), gemini_data.get("s1"))
    if not isinstance(bore, dict):
        bore = {}
    bore["tube_pitch"] = extract_tube_pitch(gemini_data.get("tube_pitch"))

    logger.info(
        "WPS analizza result: file=%s gemini=%s bore=%s",
        pdf.name,
        gemini_data,
        bore,
    )

    return JsonResponse(
        {
            "ok": True,
            "tmp_path": tmp_path,
            "wps_name": pdf.name,
            "parsed": parsed,
            "gemini": gemini_data,
            "bore": bore,
        }
    )


# ── AJAX: risorse disponibili ─────────────────────────────────────────────────


@login_required
def api_get_risorse(request: HttpRequest) -> JsonResponse:
    """Return available resources for the candidate activity.

    Query parameters: ``stabilimento``, ``data_inizio``, ``data_fine``,
    ``bore_min``, ``bore_max``, ``bore_mid``, ``tube_pitch``,
    ``modello_testa``.

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

    def _float(val: str) -> float | None:
        try:
            return float(val) if val else None
        except (ValueError, TypeError):
            return None

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
        if not relax_requirements:
            strict_requirements = True

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
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"ok": False, "error": "Body JSON non valido."}, status=400)

    tmp_path = data.get("tmp_path", "")
    if tmp_path and not default_storage.exists(tmp_path):
        return JsonResponse(
            {"ok": False, "error": "File temporaneo non trovato. Ricarica il WPS."},
            status=400,
        )

    def _dec(val) -> Decimal | None:
        if val is None or val == "":
            return None
        try:
            return Decimal(str(val))
        except InvalidOperation:
            return None

    def _float_or_none(val) -> float | None:
        """Convert a value to float, returning None for empty or invalid input.

        Args:
            val: Any value to convert.

        Returns:
            float | None: The converted value or None.
        """
        if val is None or val == "":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

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

    bore = data.get("bore", {})
    predecessor_ids = [
        str(v).strip() for v in data.get("predecessors", []) if str(v).strip()
    ]

    if predecessor_ids:
        valid_predecessors = set(
            Attivita.objects.filter(commessa=commessa).values_list(
                "activity_id", flat=True
            )
        )
        invalid = [p for p in predecessor_ids if p not in valid_predecessors]
        if invalid:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Predecessor non validi per la commessa selezionata.",
                },
                status=400,
            )

    from ui.models import Job

    def _parse_delivery_date(raw) -> str | None:
        """Normalize delivery date to ISO ``YYYY-MM-DD``.

        Accepts ISO and common Italian formats (``DD/MM/YYYY``, ``DD-MM-YYYY``).
        """
        if raw in (None, ""):
            return None
        text = str(raw).strip()
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            pass
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
        return None

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
        delivery_date = _parse_delivery_date(new_job.get("delivery_date"))
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
        if raw_cost not in (None, "") and _float_or_none(raw_cost) is None:
            return JsonResponse(
                {"ok": False, "error": "Costo totale non valido."}, status=400
            )
        raw_penalty = new_job.get("penalty")
        if raw_penalty not in (None, "") and _float_or_none(raw_penalty) is None:
            return JsonResponse(
                {"ok": False, "error": "Penale non valida."}, status=400
            )

    risorse_data = data.get("risorse", {})
    assignments_pairs = []
    for tipo, ids in [
        ("testa", risorse_data.get("teste", [])),
        ("mandrino", risorse_data.get("mandrini", [])),
        ("tastatore", risorse_data.get("tastatori", [])),
        ("maschera", risorse_data.get("maschere", [])),
        ("generatore", risorse_data.get("generatori", [])),
    ]:
        assignments_pairs.extend((tipo, r) for r in ids)

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
            bore_min=_dec(bore.get("min")),
            bore_max=_dec(bore.get("max")),
            bore_mid=_dec(bore.get("mid")),
            tube_pitch=_dec(bore.get("tube_pitch")),
            predecessors=", ".join(predecessor_ids),
        )

        if tmp_path:
            default_storage.delete(tmp_path)

        att.save()

        if new_job and new_job.get("commessa"):
            job_commessa = new_job["commessa"].strip()
            if job_commessa:
                Job.objects.get_or_create(
                    commessa=job_commessa,
                    defaults={
                        "job_id": build_job_id(job_commessa),
                        "total_cost": _float_or_none(new_job.get("total_cost")),
                        "penalty": _float_or_none(new_job.get("penalty")),
                        "delivery_date": new_job.get("delivery_date") or None,
                    },
                )

        risorsa_rows = [
            RisorsaAttivita(attivita=att, tipo=tipo, risorsa_id=rid)
            for tipo, rid in assignments_pairs
        ]
        if risorsa_rows:
            RisorsaAttivita.objects.bulk_create(risorsa_rows)

    # Detect and persist operational problems (outside atomic block so PKs are set)
    from ui.services.problem_detection import detect_and_save_problems

    warning_count = detect_and_save_problems(att, risorse_data)

    # Enqueue write-back to .mpp (outside transaction so PKs are committed)
    all_resource_ids = [r.risorsa_id for r in risorsa_rows]
    try:
        MppSyncQueue.objects.create(
            operation="add_task",
            payload_json=json.dumps(
                {
                    "attivita_id": att.pk,
                    "commessa": att.commessa,
                    "item": att.item,
                    "activity": att.activity,
                    "data_inizio": att.data_inizio.isoformat()
                    if att.data_inizio
                    else None,
                    "data_fine": att.data_fine.isoformat() if att.data_fine else None,
                    "stabilimento": att.stabilimento,
                    "responsabile": att.responsabile,
                    "resources": all_resource_ids,
                    "predecessors": att.predecessors or None,
                    "modello_testa": att.modello_testa or None,
                    "bore_min": float(att.bore_min)
                    if att.bore_min is not None
                    else None,
                    "bore_mid": float(att.bore_mid)
                    if att.bore_mid is not None
                    else None,
                    "bore_max": float(att.bore_max)
                    if att.bore_max is not None
                    else None,
                    "tube_pitch": float(att.tube_pitch)
                    if att.tube_pitch is not None
                    else None,
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).error(
            "Impossibile aggiungere alla MppSyncQueue: %s", exc
        )

    return JsonResponse({"ok": True, "id": att.pk, "warning_count": warning_count})


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
