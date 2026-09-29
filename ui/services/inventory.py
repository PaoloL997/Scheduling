"""Inventory persistence service for adding and removing resources.

Provides four public functions — :func:`insert_resource`,
:func:`update_resource`, :func:`patch_resource`, :func:`delete_resource` —
that operate on the Django ORM inventory models.
"""

from django.db import IntegrityError, transaction

_ALLOWED_TABLES = {"Generatore", "Mandrino", "Maschera", "Tastatore", "Testa"}

_TABLE_COLUMNS: dict[str, list[str]] = {
    "Generatore": ["uid", "model", "plant", "notes"],
    "Testa": ["uid", "model", "plant", "notes"],
    "Mandrino": ["uid", "diameter", "plant", "notes"],
    "Tastatore": ["uid", "diameter", "plant", "notes"],
    "Maschera": ["uid", "pitch", "plant", "center_distance"],
}

_TABLE_TO_TIPO: dict[str, str] = {
    "Testa": "testa",
    "Mandrino": "mandrino",
    "Tastatore": "tastatore",
    "Maschera": "maschera",
    "Generatore": "generatore",
}

_MAINTENANCE_TABLES = {"Testa", "Generatore"}


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


def _refresh_problems_after_resource_change(table: str, *uids: str) -> None:
    """Re-detect operational problems for activities using the changed resource."""
    tipo = _TABLE_TO_TIPO.get(table)
    if not tipo:
        return
    from ui.services.problem_detection import refresh_problems_for_resource

    refresh_problems_for_resource(tipo, *uids)


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
    maintenance = _extract_maintenance(table, data)
    if maintenance is not None:
        row["in_manutenzione"] = maintenance

    try:
        with transaction.atomic():
            model_class.objects.create(**row)
    except IntegrityError as exc:
        raise ValueError(f"uid '{data.get('uid')}' già presente in {table}.") from exc


def _normalize_field_value(val) -> str | None:
    """Normalize an inventory field value from API input."""
    if val is None:
        return None
    if isinstance(val, str):
        return val.strip() or None
    return str(val).strip() or None


def _parse_bool(val) -> bool:
    """Parse a JSON/API value into a boolean.

    Raises:
        ValueError: When *val* cannot be interpreted as a boolean.
    """
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    if isinstance(val, (int, float)) and val in (0, 1):
        return bool(val)
    text = str(val).strip().lower()
    if text in ("true", "1", "yes", "si", "sì"):
        return True
    if text in ("false", "0", "no", ""):
        return False
    raise ValueError(f"Valore booleano non valido: {val!r}")


def _extract_maintenance(table: str, data: dict) -> bool | None:
    """Return the parsed maintenance flag if present, else ``None``.

    Raises:
        ValueError: When the field is sent for a table that does not support it.
    """
    if "in_manutenzione" not in data:
        return None
    if table not in _MAINTENANCE_TABLES:
        raise ValueError("La manutenzione è disponibile solo per Testa e Generatore.")
    return _parse_bool(data["in_manutenzione"])


def patch_resource(table: str, original_uid: str, data: dict) -> str:
    """Partially update an existing resource row.

    Only keys present in ``data`` that match allowed columns are written;
    omitted fields keep their current values.

    Args:
        table: One of ``Generatore``, ``Mandrino``, ``Maschera``,
            ``Tastatore``, ``Testa``.
        original_uid: The current primary-key value of the row to update.
        data: Dictionary mapping column names to new values.

    Returns:
        str: The ``uid`` of the row after the update (may differ when renamed).

    Raises:
        ValueError: When ``table`` is not in the whitelist, when no valid
            column is supplied, when ``uid`` is empty, when no row with
            ``original_uid`` exists, or when the new ``uid`` already belongs
            to a different row.
    """
    _validated_table(table)
    model_class = _get_model(table)
    columns = _TABLE_COLUMNS[table]
    updates = {key: _normalize_field_value(data[key]) for key in data if key in columns}
    maintenance = _extract_maintenance(table, data)
    if maintenance is not None:
        updates["in_manutenzione"] = maintenance
    if not updates:
        raise ValueError("Nessun campo valido da aggiornare.")

    if "uid" in updates and not updates["uid"]:
        raise ValueError("Il campo 'uid' non può essere vuoto.")

    try:
        obj = model_class.objects.get(uid=original_uid)
    except model_class.DoesNotExist:
        raise ValueError(f"uid '{original_uid}' non trovato in {table}.") from None

    became_maintenance = bool(
        updates.get("in_manutenzione") and not getattr(obj, "in_manutenzione", False)
    )

    try:
        with transaction.atomic():
            model_class.objects.filter(uid=original_uid).update(**updates)
    except IntegrityError as exc:
        new_uid = updates.get("uid", original_uid)
        raise ValueError(f"uid '{new_uid}' già presente in {table}.") from exc

    new_uid = updates.get("uid", original_uid)
    if became_maintenance:
        from ui.services.maintenance import reassign_open_activities

        reassign_open_activities(_TABLE_TO_TIPO[table], new_uid)
    if "plant" in updates or "uid" in updates:
        _refresh_problems_after_resource_change(table, original_uid, new_uid)
    return new_uid


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
    maintenance = _extract_maintenance(table, data)

    try:
        obj = model_class.objects.get(uid=original_uid)
    except model_class.DoesNotExist:
        raise ValueError(f"uid '{original_uid}' non trovato in {table}.")

    old_plant = obj.plant
    old_uid = obj.uid
    was_maintenance = bool(getattr(obj, "in_manutenzione", False))
    for col, val in row.items():
        setattr(obj, col, val)
    if maintenance is not None:
        obj.in_manutenzione = maintenance
    elif table in _MAINTENANCE_TABLES:
        obj.in_manutenzione = False
        maintenance = False
    try:
        with transaction.atomic():
            obj.save()
    except IntegrityError as exc:
        raise ValueError(f"uid '{data.get('uid')}' già presente in {table}.") from exc

    new_uid = data.get("uid") or original_uid
    new_plant = data.get("plant")
    became_maintenance = bool(maintenance) and not was_maintenance
    if became_maintenance:
        from ui.services.maintenance import reassign_open_activities

        reassign_open_activities(_TABLE_TO_TIPO[table], new_uid)
    if new_uid != old_uid or (new_plant or "") != (old_plant or ""):
        _refresh_problems_after_resource_change(table, old_uid, new_uid)


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
