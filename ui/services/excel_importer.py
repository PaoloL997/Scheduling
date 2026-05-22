"""Excel import service for bulk-loading inventory resources.

Provides one public function that accepts an in-memory file object (Django
``InMemoryUploadedFile`` or any file-like object), parses the rows, validates
required fields, routes each row to the correct inventory table via the
``categoria`` column, and returns a tuple of ``(valid_rows, error_messages)``.

The caller is responsible for persisting the valid rows; this module only
handles parsing and validation so that it can be tested independently of the
Django ORM.

Expected column names in the unified Excel file (exactly 5 columns):

``categoria`` (required)
    One of: ``Generatore``, ``Testa``, ``Mandrino``, ``Tastatore``,
    ``Maschera``.

``uid`` (required)
    Unique resource identifier.

``misura / modello`` (required)
    Maps to: ``model`` (Generatore, Testa), ``diameter`` (Mandrino, Tastatore),
    ``pitch`` (Maschera).

``note`` (required)
    Maps to: ``notes`` (Generatore, Testa, Mandrino, Tastatore),
    ``center_distance`` (Maschera — interasse).

``stabilimento`` (required)
    Maps to ``plant`` for all categories.
"""

from __future__ import annotations

import logging
from typing import Any

import openpyxl

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = {"Generatore", "Testa", "Mandrino", "Tastatore", "Maschera"}

# The five column names that must appear in the Excel header.
_EXCEL_COLUMNS = ["categoria", "uid", "misura / modello", "note", "stabilimento"]

# For each category: maps Excel column name → ORM field name.
_CATEGORY_FIELD_MAP: dict[str, dict[str, str]] = {
    "Generatore": {
        "misura / modello": "model",
        "note": "notes",
        "stabilimento": "plant",
    },
    "Testa": {"misura / modello": "model", "note": "notes", "stabilimento": "plant"},
    "Mandrino": {
        "misura / modello": "diameter",
        "note": "notes",
        "stabilimento": "plant",
    },
    "Tastatore": {
        "misura / modello": "diameter",
        "note": "notes",
        "stabilimento": "plant",
    },
    "Maschera": {
        "misura / modello": "pitch",
        "note": "center_distance",
        "stabilimento": "plant",
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_inventory_excel_generic(
    file_obj: Any,
) -> tuple[list[dict], list[str]]:
    """Parse a unified Excel file by column position (header row is skipped).

    Column order (1-based):
      1. categoria   — Generatore | Testa | Mandrino | Tastatore | Maschera
      2. uid         — unique resource identifier
      3. misura      — model / diameter / pitch depending on category
      4. note        — free text notes; for Maschera: centre distance (interasse)
      5. stabilimento — plant

    The first row is always skipped (treated as a header regardless of content).
    """
    try:
        wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    except Exception as exc:
        return [], [f"Impossibile leggere il file Excel: {exc}"]

    try:
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)

        try:
            next(rows_iter)
        except StopIteration:
            return [], ["Il file è vuoto."]

        valid_rows: list[dict] = []
        errors: list[str] = []

        for row_num, row in enumerate(rows_iter, start=2):
            cells = list(row) + [None] * 5

            if all(v is None or str(v).strip() == "" for v in cells[:5]):
                continue

            categoria = _str_or_none(cells[0])
            uid = _str_or_none(cells[1])
            misura = _str_or_none(cells[2])
            plant = _str_or_none(cells[3])
            note = _str_or_none(cells[4])

            if not categoria:
                errors.append(
                    f"Riga {row_num}: colonna 1 (categoria) vuota — riga ignorata."
                )
                continue
            if categoria not in _VALID_CATEGORIES:
                errors.append(
                    f"Riga {row_num}: categoria '{categoria}' non valida "
                    f"(ammessi: {', '.join(sorted(_VALID_CATEGORIES))}) — riga ignorata."
                )
                continue
            if not uid:
                errors.append(f"Riga {row_num}: colonna 2 (uid) vuota — riga ignorata.")
                continue

            field_map = _CATEGORY_FIELD_MAP[categoria]
            coerced: dict[str, Any] = {
                "_table": categoria,
                "uid": uid,
                field_map["misura / modello"]: misura,
                field_map["note"]: note,
                field_map["stabilimento"]: plant,
            }

            valid_rows.append(coerced)

    finally:
        wb.close()

    return valid_rows, errors


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _str_or_none(val: Any) -> str | None:  # noqa: ANN401
    """Convert a cell value to a stripped string, or None if empty.

    Args:
        val: Raw cell value from openpyxl.

    Returns:
        str | None: Cleaned string or None.
    """
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None
