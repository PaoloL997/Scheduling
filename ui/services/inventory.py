"""Inventory persistence service for adding and removing resources.

Provides three public functions — :func:`insert_resource`,
:func:`update_resource`, :func:`delete_resource` — that operate on the Django
ORM inventory models.  After each successful mutation a corresponding entry is
added to :class:`~ui.models.MppSyncQueue` so the change is written back to the
``.mpp`` source file when the last user logs out.
"""

import json

from django.db import IntegrityError

_ALLOWED_TABLES = {"Generatore", "Mandrino", "Maschera", "Tastatore", "Testa"}

_TABLE_COLUMNS: dict[str, list[str]] = {
    "Generatore": ["uid", "model", "plant", "notes"],
    "Testa": ["uid", "model", "plant", "notes"],
    "Mandrino": ["uid", "diameter", "plant", "notes"],
    "Tastatore": ["uid", "diameter", "plant", "notes"],
    "Maschera": ["uid", "pitch", "plant", "center_distance"],
}

_TABLE_MEASURE_COL: dict[str, str] = {
    "Generatore": "model",
    "Testa": "model",
    "Mandrino": "diameter",
    "Tastatore": "diameter",
    "Maschera": "pitch",
}

# For Maschera the human-readable note is stored in `center_distance`, not `notes`.
_TABLE_NOTES_COL: dict[str, str] = {
    "Generatore": "notes",
    "Testa": "notes",
    "Mandrino": "notes",
    "Tastatore": "notes",
    "Maschera": "center_distance",
}


def _get_model(table: str):
    """Return the ORM model class for the given inventory table name.

    Args:
        table: One of the five allowed inventory table names.

    Returns:
        The Django model class.
    """
    from ui.models import (
        GeneratoreItem,
        MandrinoItem,
        MascheraItem,
        TastatoreItem,
        TestaItem,
    )

    return {
        "Generatore": GeneratoreItem,
        "Mandrino": MandrinoItem,
        "Maschera": MascheraItem,
        "Tastatore": TastatoreItem,
        "Testa": TestaItem,
    }[table]


def _validated_table(table: str) -> str:
    """Return ``table`` unchanged if it is in the whitelist.

    Args:
        table: The table name supplied by the caller.

    Returns:
        str: The validated table name.

    Raises:
        ValueError: When ``table`` is not one of the five allowed inventory tables.
    """
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"Tabella non valida: {table!r}")
    return table


def insert_resource(table: str, data: dict) -> None:
    """Insert a new resource row into the given inventory table.

    Args:
        table: One of ``Generatore``, ``Mandrino``, ``Maschera``,
            ``Tastatore``, ``Testa``.
        data: Dictionary mapping column names to values.

    Raises:
        ValueError: When ``table`` is not in the whitelist or when a row
            with the same ``uid`` already exists.
    """
    _validated_table(table)
    model_class = _get_model(table)
    columns = _TABLE_COLUMNS[table]
    row = {col: data.get(col) or None for col in columns}

    try:
        model_class.objects.create(**row)
    except IntegrityError as exc:
        raise ValueError(f"uid '{data.get('uid')}' già presente in {table}.") from exc

    measure_col = _TABLE_MEASURE_COL.get(table)
    _enqueue_add_resource(table, data, measure_col)


def update_resource(table: str, original_uid: str, data: dict) -> None:
    """Update an existing resource row, including optionally renaming its ``uid``.

    Args:
        table: One of ``Generatore``, ``Mandrino``, ``Maschera``,
            ``Tastatore``, ``Testa``.
        original_uid: The current primary-key value of the row to update.
        data: Dictionary mapping column names to new values.

    Raises:
        ValueError: When ``table`` is not in the whitelist, when no row with
            ``original_uid`` exists, or when the new ``uid`` already belongs
            to a different row.
    """
    _validated_table(table)
    model_class = _get_model(table)
    columns = _TABLE_COLUMNS[table]
    row = {col: data.get(col) or None for col in columns}

    try:
        obj = model_class.objects.get(uid=original_uid)
    except model_class.DoesNotExist:
        raise ValueError(f"uid '{original_uid}' non trovato in {table}.")

    for col, val in row.items():
        setattr(obj, col, val)
    try:
        obj.save()
    except IntegrityError as exc:
        raise ValueError(f"uid '{data.get('uid')}' già presente in {table}.") from exc

    _enqueue_update_resource(table, original_uid, data)


def delete_resource(table: str, uid: str) -> None:
    """Delete a resource row identified by its ``uid``.

    Args:
        table: One of ``Generatore``, ``Mandrino``, ``Maschera``,
            ``Tastatore``, ``Testa``.
        uid: The primary-key value of the row to delete.

    Raises:
        ValueError: When ``table`` is not in the whitelist or when no row
            with the given ``uid`` exists.
    """
    _validated_table(table)
    model_class = _get_model(table)

    deleted, _ = model_class.objects.filter(uid=uid).delete()
    if deleted == 0:
        raise ValueError(f"uid '{uid}' non trovato in {table}.")

    _enqueue_delete_resource(table, uid)


# ---------------------------------------------------------------------------
# Sync-queue helpers
# ---------------------------------------------------------------------------


def _enqueue_add_resource(table: str, data: dict, measure_col: str | None) -> None:
    """Add an ``add_resource`` entry to :class:`~ui.models.MppSyncQueue`."""
    notes_col = _TABLE_NOTES_COL.get(table, "notes")
    try:
        from ui.models import MppSyncQueue

        MppSyncQueue.objects.create(
            operation="add_resource",
            payload_json=json.dumps(
                {
                    "table": table,
                    "uid": data.get("uid"),
                    "measure": data.get(measure_col) if measure_col else None,
                    "plant": data.get("plant"),
                    "notes": data.get(notes_col),
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).error(
            "Impossibile aggiungere alla MppSyncQueue: %s", exc
        )


def _enqueue_delete_resource(table: str, uid: str) -> None:
    """Add a ``delete_resource`` entry to :class:`~ui.models.MppSyncQueue`."""
    try:
        from ui.models import MppSyncQueue

        MppSyncQueue.objects.create(
            operation="delete_resource",
            payload_json=json.dumps({"table": table, "uid": uid}),
        )
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).error(
            "Impossibile aggiungere alla MppSyncQueue: %s", exc
        )


def _enqueue_update_resource(table: str, original_uid: str, data: dict) -> None:
    """Add an ``update_resource`` entry to :class:`~ui.models.MppSyncQueue`."""
    try:
        from ui.models import MppSyncQueue

        measure_col = _TABLE_MEASURE_COL.get(table)
        notes_col = _TABLE_NOTES_COL.get(table, "notes")
        MppSyncQueue.objects.create(
            operation="update_resource",
            payload_json=json.dumps(
                {
                    "table": table,
                    "original_uid": original_uid,
                    "uid": data.get("uid"),
                    "measure": data.get(measure_col) if measure_col else None,
                    "plant": data.get("plant"),
                    "notes": data.get(notes_col),
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).error(
            "Impossibile aggiungere alla MppSyncQueue: %s", exc
        )
