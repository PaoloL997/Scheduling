"""MPP import trigger for the tablet bridge API."""

from __future__ import annotations

from ui.models import MppSyncQueue
from ui.services.importer import ImportStatus, get_status, trigger_reimport
from ui.services.reset import reset_app_dataset


def request_import(*, force: bool = False) -> dict:
    """Start a fresh import from the configured source files.

    Args:
        force: When ``True``, discard pending ``MppSyncQueue`` entries and
            re-import even if the dataset is already ``READY``.

    Returns:
        dict: ``{"accepted": bool, "status": str, "message": str}``.
    """
    status = get_status()
    if status == ImportStatus.LOADING:
        return {
            "accepted": False,
            "status": status.value,
            "message": "Import già in corso.",
        }
    if status == ImportStatus.WRITING:
        return {
            "accepted": False,
            "status": status.value,
            "message": "Write-back in corso.",
        }
    if status == ImportStatus.READY and not force and MppSyncQueue.objects.exists():
        return {
            "accepted": False,
            "status": status.value,
            "message": "Modifiche pendenti. Usa force=true per sovrascrivere.",
        }

    reset_app_dataset(force=True)
    trigger_reimport()
    return {
        "accepted": True,
        "status": ImportStatus.LOADING.value,
        "message": "Import avviato.",
    }
