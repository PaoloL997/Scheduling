"""Single write path for the operations audit log.

Every human-facing mutating endpoint (web or tablet) and every automatic
problem-detection / auto-resolution / auto-reassignment event calls
:func:`record_operation` exactly once.  Persistence failures must never
break the real operation that triggered them, so this is the one place in
the module allowed to swallow an exception.
"""

from __future__ import annotations

import logging

from django.http import HttpRequest

logger = logging.getLogger(__name__)


_FIELD_LABELS: dict[str, str] = {
    "actual_start": "inizio effettivo",
    "actual_finish": "fine effettiva",
    "start": "data inizio",
    "finish": "data fine",
    "plant": "stabilimento",
    "responsible": "responsabile",
    "risorse": "risorse",
    "predecessors": "predecessori",
    "tp60_custom": "sporgenza personalizzata",
    "commessa": "commessa",
    "item": "item",
    "activity": "attività",
}


def record_operation(
    request: HttpRequest | None = None,
    *,
    azione: str,
    descrizione: str,
    riferimento: str = "",
    user=None,
    source: str | None = None,
) -> None:
    """Append one entry to :class:`~ui.models.OperationLog`.

    Args:
        request: The originating request, when available.  Used to derive
            ``source`` (``tablet`` when ``request.path`` starts with
            ``/api/bridge/``, else ``web``) and ``user`` when neither is
            passed explicitly.
        azione: Short slug identifying the kind of operation
            (e.g. ``attivita_modifica``, ``inventario_crea``).
        descrizione: Italian, human-readable summary of what happened.
        riferimento: Optional identity text (commessa/item/activity or
            resource uid) for readability and future filtering.
        user: Explicit user override; takes precedence over ``request.user``.
        source: Explicit source override (``"sistema"`` for automatic
            events, which have no request in scope). Takes precedence over
            request-path sniffing.

    Returns:
        None. Never raises: persistence failures are logged and dropped so
        a broken log write can never fail the real operation.
    """
    try:
        from ui.models import OperationLog

        effective_user = user
        if effective_user is None and request is not None:
            candidate = getattr(request, "user", None)
            if candidate is not None and candidate.is_authenticated:
                effective_user = candidate

        effective_source = source
        if effective_source is None:
            if request is not None and request.path.startswith("/api/bridge/"):
                effective_source = OperationLog.SOURCE_TABLET
            else:
                effective_source = OperationLog.SOURCE_WEB

        username = effective_user.username if effective_user is not None else "Sistema"

        OperationLog.objects.create(
            user=effective_user,
            username=username,
            source=effective_source,
            azione=azione,
            descrizione=descrizione,
            riferimento=riferimento,
        )
    except Exception:  # noqa: BLE001 — logging must never break the caller.
        logger.exception("Impossibile registrare l'operazione nel log: %s", azione)


def describe_attivita_update(data: dict) -> str:
    """Return an Italian summary of which activity fields an update touched.

    Args:
        data: The payload passed to
            :func:`~ui.services.bridge_write.update_attivita`.

    Returns:
        str: ``"Modificati: <campo>, <campo>..."``, or a generic fallback
            when no recognised key is present.
    """
    changed = [label for key, label in _FIELD_LABELS.items() if key in data]
    if not changed:
        return "Nessun campo modificato"
    return "Modificati: " + ", ".join(changed)
