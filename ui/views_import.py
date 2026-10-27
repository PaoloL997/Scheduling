"""AJAX endpoint for bulk-importing inventory resources from a unified Excel file.

Accepts a ``multipart/form-data`` POST with a single ``file`` field containing
a ``.xlsx`` workbook.  Each row must include a ``categoria`` column that
determines which inventory table it belongs to.  Delegates parsing and
validation to :mod:`ui.services.excel_importer` and persists valid rows via
the ORM.

Response schema (success)::

    {"ok": true, "imported": N, "skipped": M, "errors": ["Riga 3: …", …]}

Response schema (fatal parse error)::

    {"ok": false, "error": "<message>"}
"""

import logging

from django.db import IntegrityError, transaction
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ui.permissions import write_required
from ui.services.excel_importer import parse_inventory_excel_generic
from ui.services.inventory import (
    _TABLE_COLUMNS,
    _get_model,
    normalize_measure_columns,
)

logger = logging.getLogger(__name__)


@write_required
@csrf_exempt
@require_http_methods(["POST"])
def api_inventario_import_excel(request: HttpRequest) -> JsonResponse:
    """Bulk-insert inventory resources from a unified uploaded Excel file.

    Expects ``multipart/form-data`` with a ``file`` field containing a
    ``.xlsx`` workbook where each row carries a ``categoria`` column that
    determines the target inventory table.  Rows that fail validation are
    reported in ``errors`` but do not block valid rows.  Duplicate ``uid``
    values within a category are skipped and counted as ``skipped``.

    Args:
        request: The incoming POST request.

    Returns:
        JsonResponse: ``{ok, imported, skipped, errors}`` on success;
        ``{ok: false, error}`` on fatal error.
    """
    uploaded = request.FILES.get("file")
    if not uploaded:
        logger.warning("import-excel: nessun file allegato")
        return JsonResponse({"ok": False, "error": "Nessun file allegato."}, status=400)
    if not uploaded.name.lower().endswith(".xlsx"):
        logger.warning("import-excel: file non .xlsx (%s)", uploaded.name)
        return JsonResponse(
            {"ok": False, "error": "Il file deve essere in formato .xlsx."}, status=400
        )

    valid_rows, errors = parse_inventory_excel_generic(uploaded)
    logger.info(
        "import-excel: parse completato — %d righe valide, %d errori",
        len(valid_rows),
        len(errors),
    )
    for e in errors:
        logger.warning("import-excel parse error: %s", e)

    if not valid_rows and errors:
        logger.warning("import-excel: nessuna riga valida, primo errore: %s", errors[0])
        return JsonResponse({"ok": False, "error": errors[0]}, status=400)

    imported = 0
    skipped = 0
    details: dict[str, int] = {}

    for row in valid_rows:
        table = row["_table"]
        model_class = _get_model(table)
        known_cols = _TABLE_COLUMNS[table]
        obj_kwargs = normalize_measure_columns(
            table, {col: row.get(col) for col in known_cols}
        )
        try:
            with transaction.atomic():
                model_class.objects.create(**obj_kwargs)
            imported += 1
            details[table] = details.get(table, 0) + 1
        except IntegrityError:
            logger.warning(
                "import-excel: uid '%s' già presente in %s — saltato",
                row.get("uid"),
                table,
            )
            skipped += 1

    logger.info("import-excel: importate %d righe, saltate %d", imported, skipped)
    return JsonResponse(
        {
            "ok": True,
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "details": details,
        }
    )
