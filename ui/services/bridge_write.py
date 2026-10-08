"""Write operations shared by the web API and the tablet bridge."""

from __future__ import annotations

import datetime
import logging

from django.db import transaction
from django.utils.timezone import localdate

from ui.models import Attivita, RisorsaAttivita
from ui.services.resource_conflicts import (
    find_conflicts,
    occupation_window,
    format_conflict_message,
)
from ui.services.resource_types import (
    CATEGORIE,
    pairs_to_risorse_dict,
    risorse_dict_to_pairs,
)
from ui.services.resource_display import (
    format_activity_resources,
    format_activity_resources_html,
)

logger = logging.getLogger(__name__)


def _merge_risorse_with_existing(attivita_id: int, risorse_data: dict) -> dict:
    """Return a full five-category risorse dict for a (possibly partial) update.

    The resource update replaces every assignment of the activity, so a
    request that names only the categories it touched would otherwise wipe
    the others without any error.  A category **absent** from the request
    keeps its current assignments; a category present with an empty list is
    an explicit clear.

    Args:
        attivita_id: Primary key of the activity being updated.
        risorse_data: Category name to list of UIDs, possibly partial.

    Returns:
        dict: Every category key present, merged with the stored assignments.
    """
    existing = pairs_to_risorse_dict(
        RisorsaAttivita.objects.filter(attivita_id=attivita_id).values_list(
            "tipo", "risorsa_id"
        )
    )

    merged: dict[str, list[str]] = {}
    for key in CATEGORIE:
        if key in risorse_data:
            merged[key] = [str(r) for r in (risorse_data.get(key) or []) if r]
        else:
            merged[key] = existing[key]
    return merged


class BridgeWriteError(Exception):
    """Raised when a bridge write operation fails validation."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        conflicts: list | None = None,
        missing_resources: list | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.conflicts = conflicts
        self.missing_resources = missing_resources


def _parse_date(val, field: str = "data"):
    """Parse an ISO date, telling an intentional clear from a bad value.

    An absent or empty value clears the field; anything unparsable is a
    client error rather than a silent clear.

    Args:
        val: Raw value from the request payload.
        field: Field name used in the error message.

    Returns:
        datetime.date | None: The parsed date, or ``None`` to clear.

    Raises:
        BridgeWriteError: When *val* is a non-empty unparsable value.
    """
    if val is None:
        return None
    text = str(val).strip()
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError as exc:
        raise BridgeWriteError(
            f"Data non valida per «{field}»: «{text}». Formato atteso AAAA-MM-GG.",
            status_code=400,
        ) from exc


def format_future_start_message(
    actual_start: datetime.date, today: datetime.date
) -> str:
    """Explain why an «inizio effettivo» dated after today is refused.

    Args:
        actual_start: The date the operator typed.
        today: The reference day, i.e. the latest date accepted.

    Returns:
        str: The message shown to the operator.
    """
    return (
        f"L'inizio effettivo non può essere una data futura: "
        f"«{actual_start.isoformat()}» viene dopo oggi ({today.isoformat()}). "
        f"Indica la data odierna o una data passata."
    )


def validate_actual_start_not_future(
    actual_start: datetime.date | None, *, today: datetime.date | None = None
) -> None:
    """Refuse an «inizio effettivo» that has not happened yet.

    Recording a start records a fact, and a fact cannot be dated on a day that
    has not come: the operator may only write today or a past date.  This is
    the single definition of the rule; both the web API and the tablet bridge
    reach it through :func:`update_attivita`.

    Args:
        actual_start: The parsed date, or ``None`` when the field is cleared.
        today: Reference day, defaulting to the server's current date.

    Raises:
        BridgeWriteError: 400 when *actual_start* is after *today*.
    """
    if actual_start is None:
        return
    reference = today if today is not None else localdate()
    if actual_start > reference:
        raise BridgeWriteError(
            format_future_start_message(actual_start, reference),
            status_code=400,
        )


def update_attivita(orm_id: int, data: dict, *, resolve_overlaps: bool = True) -> dict:
    """Update a single :class:`~ui.models.Attivita`.

    Args:
        orm_id: Primary key of the activity.
        data: Field updates (same keys as ``api_tasks_update``).
        resolve_overlaps: When True, auto-swap delay overlaps after the write.

    Returns:
        dict: Success payload with ``item``, ``job_id``, ``activity_id``.

    Raises:
        BridgeWriteError: On validation or not-found errors.
    """
    try:
        a = Attivita.objects.get(pk=orm_id)
    except Attivita.DoesNotExist as exc:
        raise BridgeWriteError("Attività non trovata.", status_code=404) from exc

    old_commessa = a.commessa
    old_item = a.item
    old_activity = a.activity

    if "commessa" in data:
        new_commessa = (data["commessa"] or "").strip()
        if new_commessa:
            a.commessa = new_commessa
    if "item" in data:
        item_value = str(data["item"]).strip()
        if not item_value:
            raise BridgeWriteError(
                "Il valore di item è obbligatorio.",
                status_code=400,
            )
        a.item = item_value
    if "activity" in data:
        from ui.services.activity_inventory import validate_activity_type

        try:
            a.activity = validate_activity_type(data["activity"])
        except ValueError as exc:
            raise BridgeWriteError(str(exc), status_code=400) from exc

    if a.commessa != old_commessa or a.item != old_item or a.activity != old_activity:
        if (
            Attivita.objects.filter(
                commessa=a.commessa, item=a.item, activity=a.activity
            )
            .exclude(pk=a.pk)
            .exists()
        ):
            raise BridgeWriteError(
                "Esiste già un'attività con questa combinazione "
                "commessa/item/attività.",
                status_code=409,
            )

    old_inizio, old_fine = a.data_inizio, a.data_fine
    old_actual_start = a.actual_start
    if "start" in data:
        a.data_inizio = _parse_date(data["start"], "Inizio")
    if "finish" in data:
        a.data_fine = _parse_date(data["finish"], "Fine")
    if "actual_start" in data:
        a.actual_start = _parse_date(data["actual_start"], "Inizio effettivo")
        # Only a *new* value is checked: the pianificazione panel echoes the
        # field back untouched, so an activity already carrying a future start
        # (imported from MS Project before the rule existed) stays editable.
        if a.actual_start != old_actual_start:
            validate_actual_start_not_future(a.actual_start)
    if "actual_finish" in data:
        a.actual_finish = _parse_date(data["actual_finish"], "Fine effettiva")

    # The planned window is only "edited" when it actually moves: the
    # pianificazione panel always echoes start/finish back, even untouched.
    plan_dates_changed = (a.data_inizio, a.data_fine) != (old_inizio, old_fine)

    if a.data_inizio and a.data_fine and a.data_fine < a.data_inizio:
        raise BridgeWriteError(
            "La fine prevista non puo' precedere l'inizio previsto.",
            status_code=400,
        )
    if a.actual_finish and a.actual_start and a.actual_finish < a.actual_start:
        raise BridgeWriteError(
            "La fine effettiva non puo' precedere l'inizio effettivo.",
            status_code=400,
        )
    if "plant" in data:
        a.stabilimento = data["plant"] or ""
    if "responsible" in data:
        a.responsabile = data["responsible"] or ""
    if "tp60_custom" in data:
        a.tp60_custom = bool(data["tp60_custom"])
    predecessor_objs: list | None = None
    if "predecessors" in data:
        from ui.services.precedences import resolve_predecessors

        try:
            predecessor_objs = resolve_predecessors(
                a.commessa, data["predecessors"], exclude_pk=a.pk
            )
        except ValueError as exc:
            raise BridgeWriteError(str(exc), status_code=400) from exc
    elif a.commessa != old_commessa:
        # Moved to another commessa: the old precedences no longer apply.
        predecessor_objs = []

    risorse_data = data.get("risorse")
    new_risorse_pairs: list[tuple[str, str]] | None = None
    if risorse_data is not None:
        if not isinstance(risorse_data, dict):
            raise BridgeWriteError("Formato risorse non valido.", status_code=400)
        risorse_data = _merge_risorse_with_existing(a.pk, risorse_data)
        new_risorse_pairs = risorse_dict_to_pairs(risorse_data)
        if new_risorse_pairs and occupation_window(a) is None:
            raise BridgeWriteError(
                "Per assegnare risorse servono data di inizio e fine.",
                status_code=400,
            )

    # Re-planning is validated up front; recording a fact never is.  Setting
    # «Inizio/Fine effettiva» states what already happened, so a *resource
    # conflict* can be surfaced but must never refuse the save -- it becomes a
    # note plus a reschedule proposal on the activity that has not started yet.
    # The one exception is the minimum-resources rule below: an activity
    # cannot be launched at all without its minimum kit.
    existing_pairs = (
        new_risorse_pairs
        if new_risorse_pairs is not None
        else list(
            RisorsaAttivita.objects.filter(attivita_id=a.pk).values_list(
                "tipo", "risorsa_id"
            )
        )
    )
    # Starting is not a fact that can simply be recorded: an activity may not
    # be launched before the minimum set of resources is booked (one testa,
    # one generatore and, per testa model, the minimum of each accessory).
    # Only the transition to «avviata» is guarded, so unrelated edits on an
    # activity started before the rule existed keep working.
    if a.actual_start is not None and old_actual_start is None:
        from ui.services.problem_detection import (
            format_missing_minimum_message,
            missing_minimum_resources,
        )

        shortfalls = missing_minimum_resources(a, existing_pairs)
        if shortfalls:
            raise BridgeWriteError(
                format_missing_minimum_message(shortfalls),
                status_code=409,
                missing_resources=shortfalls,
            )

    # One conflict check against the window the activity will really hold.
    # A new resource selection or a moved planned window is a re-planning and
    # is refused on conflict; a recorded fact only carries a warning.
    window = occupation_window(a)
    warnings: list[dict] = []
    if existing_pairs and window is not None:
        conflicts = find_conflicts(
            existing_pairs, window[0], window[1], exclude_activity_id=a.pk
        )
        if conflicts and (new_risorse_pairs is not None or plan_dates_changed):
            hint = (
                "Scegli risorse alternative non occupate nel periodo indicato."
                if new_risorse_pairs is not None
                else "Usa «Modifica risorse» per sostituirle o rimuoverle, "
                "oppure accorcia il periodo dell'attività."
            )
            raise BridgeWriteError(
                format_conflict_message(conflicts, action_hint=hint),
                status_code=409,
                conflicts=conflicts,
            )
        warnings = conflicts

    with transaction.atomic():
        a.save()
        if predecessor_objs is not None:
            a.predecessori.set(predecessor_objs)

        if new_risorse_pairs is not None:
            RisorsaAttivita.objects.filter(attivita_id=a.pk).delete()
            risorsa_rows = [
                RisorsaAttivita(attivita=a, tipo=tipo, risorsa_id=rid)
                for tipo, rid in new_risorse_pairs
            ]
            if risorsa_rows:
                RisorsaAttivita.objects.bulk_create(risorsa_rows)

        # The notes are part of the same write: a failure here rolls back
        # the activity too, never leaving the two out of step.
        if risorse_data is not None:
            from ui.services.problem_detection import detect_and_save_problems

            detect_and_save_problems(a, risorse_data)
        elif "plant" in data or "tp60_custom" in data:
            from ui.services.problem_detection import refresh_problems_for_attivita

            refresh_problems_for_attivita(a)

    result = {
        "item": a.item,
        "job_id": a.job_id,
        "activity_id": a.activity_id,
    }
    if warnings:
        result["warnings"] = warnings
        result["warning_message"] = format_conflict_message(
            warnings,
            action_hint=(
                "La registrazione e' stata salvata. Il conflitto e' segnalato "
                "sull'attività non ancora iniziata, con proposta di "
                "ripianificazione."
            ),
        )
    if risorse_data is not None:
        risorse_all = list(RisorsaAttivita.objects.filter(attivita_id=a.pk))
        res_fmt = format_activity_resources(risorse_all)
        result.update(
            {
                "resources": ";".join(res_fmt["flat_ids"]) or None,
                "resources_display": res_fmt["display"],
                "resources_by_tipo": res_fmt["by_tipo"],
                "resources_html": format_activity_resources_html(risorse_all),
            }
        )
    if resolve_overlaps:
        should_resolve = risorse_data is not None or any(
            key in data for key in ("actual_start", "actual_finish", "start", "finish")
        )
        if should_resolve:
            from ui.services.overlap_reassign import auto_resolve_resource_overlaps

            result.update(auto_resolve_resource_overlaps())
    return result


def delete_attivita(orm_id: int) -> None:
    """Delete an activity together with its resources and problems.

    Args:
        orm_id: Primary key of the activity.

    Raises:
        BridgeWriteError: When the activity does not exist.
    """
    try:
        a = Attivita.objects.get(pk=orm_id)
    except Attivita.DoesNotExist as exc:
        raise BridgeWriteError("Attività non trovata.", status_code=404) from exc

    a.delete()

    from ui.services.reconcile import reconcile_after_delete

    reconcile_after_delete()
