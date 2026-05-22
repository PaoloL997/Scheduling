"""Export the current Job table to an Excel (.xlsx) file as a full overwrite.

Called by :mod:`ui.services.project_session` after writing back changes to the
``.mpp`` file, on the last authenticated user's logout.  The export always
rewrites ``Sheet1`` from scratch so the file is always consistent with the DB.
"""

import logging
from pathlib import Path

import openpyxl

logger = logging.getLogger(__name__)

_HEADERS = ["Commessa", "Costo Totale", "Penalità", "Data consegna prevista"]


def export_to_xlsx(xlsx_path: str) -> None:
    """Overwrite Sheet1 with the current contents of the :class:`~ui.models.Job` table.

    Loads the existing workbook if the file exists, otherwise creates a new
    one.  ``Sheet1`` is cleared and rewritten from scratch so the output is
    always consistent with the DB regardless of the prior file state.
    All other sheets are preserved untouched.

    Args:
        xlsx_path: Absolute or relative path to the ``.xlsx`` file.
                   The file is created if it does not exist.
    """
    from ui.models import Job

    path = Path(xlsx_path)
    if path.exists():
        try:
            wb = openpyxl.load_workbook(str(path))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Impossibile aprire il file xlsx esistente (%s) — ne creo uno nuovo.",
                exc,
            )
            wb = openpyxl.Workbook()
    else:
        wb = openpyxl.Workbook()

    if "Sheet1" in wb.sheetnames:
        ws = wb["Sheet1"]
        ws.delete_rows(1, ws.max_row or 1)
    else:
        ws = wb.create_sheet("Sheet1", 0)
        # openpyxl creates a default "Sheet" on a new workbook; remove it if empty
        for name in list(wb.sheetnames):
            if name != "Sheet1" and name.startswith("Sheet"):
                candidate = wb[name]
                if (candidate.max_row or 0) <= 1 and (candidate.max_column or 0) <= 1:
                    del wb[name]
                    break

    ws.append(_HEADERS)
    for job in Job.objects.order_by("commessa"):
        ws.append(
            [
                job.commessa,
                job.total_cost,
                job.penalty,
                job.delivery_date,
            ]
        )

    wb.save(str(path))
    logger.info(
        "Export xlsx completato: %d commesse scritte in '%s'.",
        Job.objects.count(),
        path.name,
    )
