"""Tests for src.data.export_xlsx.export_to_xlsx."""

import os
import tempfile

import openpyxl
from django.test import TestCase

from ui.models import Job


def _run_export(xlsx_path: str) -> None:
    from src.data.export_xlsx import export_to_xlsx

    export_to_xlsx(xlsx_path)


class ExportXlsxCreatesFileTest(TestCase):
    """export_to_xlsx creates the file when it does not exist."""

    def test_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "output.xlsx")
            _run_export(path)
            self.assertTrue(os.path.exists(path))

    def test_creates_file_with_sheet1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "output.xlsx")
            _run_export(path)
            wb = openpyxl.load_workbook(path)
            self.assertIn("Sheet1", wb.sheetnames)


class ExportXlsxHeadersTest(TestCase):
    """Correct headers are written on row 1."""

    def test_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "output.xlsx")
            _run_export(path)
            wb = openpyxl.load_workbook(path)
            ws = wb["Sheet1"]
            row = [cell.value for cell in ws[1]]
            self.assertEqual(
                row,
                [
                    "Job ID",
                    "Commessa",
                    "Costo Totale",
                    "Penalità",
                    "Data consegna prevista",
                ],
            )


class ExportXlsxDataTest(TestCase):
    """Job rows are written correctly."""

    def setUp(self) -> None:
        Job.objects.create(
            commessa="A001",
            total_cost=100000.0,
            penalty=5.0,
            delivery_date="2025-12-31",
        )
        Job.objects.create(
            commessa="B002",
            total_cost=200000.0,
            penalty=10.0,
            delivery_date="2026-06-30",
        )

    def test_row_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "output.xlsx")
            _run_export(path)
            wb = openpyxl.load_workbook(path)
            ws = wb["Sheet1"]
            # max_row includes header
            self.assertEqual(ws.max_row, 3)

    def test_row_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "output.xlsx")
            _run_export(path)
            wb = openpyxl.load_workbook(path)
            ws = wb["Sheet1"]
            rows = list(ws.iter_rows(min_row=2, values_only=True))
            commesse = [r[1] for r in rows]
            self.assertIn("A001", commesse)
            self.assertIn("B002", commesse)

    def test_empty_table_writes_only_header(self) -> None:
        Job.objects.all().delete()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "output.xlsx")
            _run_export(path)
            wb = openpyxl.load_workbook(path)
            ws = wb["Sheet1"]
            # Only header row
            self.assertEqual(ws.max_row, 1)


class ExportXlsxOverwriteTest(TestCase):
    """Sheet1 is fully overwritten on repeated calls."""

    def setUp(self) -> None:
        Job.objects.create(
            commessa="X001", total_cost=50000.0, penalty=2.0, delivery_date="2025-01-01"
        )

    def test_overwrite_removes_old_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "output.xlsx")

            # First export with existing job
            _run_export(path)

            # Delete the job, add a new one
            Job.objects.all().delete()
            Job.objects.create(
                commessa="Y002",
                total_cost=75000.0,
                penalty=3.0,
                delivery_date="2025-06-01",
            )

            # Second export should reflect new state
            _run_export(path)

            wb = openpyxl.load_workbook(path)
            ws = wb["Sheet1"]
            rows = list(ws.iter_rows(min_row=2, values_only=True))
            commesse = [r[1] for r in rows]
            self.assertNotIn("X001", commesse)
            self.assertIn("Y002", commesse)


class ExportXlsxPreservesOtherSheetsTest(TestCase):
    """Other sheets in the workbook are not touched."""

    def setUp(self) -> None:
        Job.objects.create(
            commessa="Z001", total_cost=10000.0, penalty=1.0, delivery_date="2025-03-15"
        )

    def test_other_sheet_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "output.xlsx")

            # Create a workbook with an extra sheet
            wb = openpyxl.Workbook()
            ws_extra = wb.create_sheet("ExtraData")
            ws_extra.append(["Important", "Data"])
            wb.save(path)

            _run_export(path)

            wb2 = openpyxl.load_workbook(path)
            self.assertIn("ExtraData", wb2.sheetnames)
            self.assertEqual(wb2["ExtraData"]["A1"].value, "Important")
