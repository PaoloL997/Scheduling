"""Tests for MS Project COM date/time conversion in project.py."""

from __future__ import annotations

from datetime import date, datetime

from django.test import SimpleTestCase

from src.data.project import (
    _MSP_CLEAR_DATE,
    _to_msp_clear_datetime,
    _to_msp_finish_datetime,
    _to_msp_start_datetime,
)


class MspDatetimeConversionTest(SimpleTestCase):
    def test_start_datetime_uses_nine_am(self) -> None:
        result = _to_msp_start_datetime(date(2026, 8, 4))
        self.assertEqual(result, datetime(2026, 8, 4, 9, 0))

    def test_finish_datetime_uses_six_pm(self) -> None:
        result = _to_msp_finish_datetime(date(2026, 8, 3))
        self.assertEqual(result, datetime(2026, 8, 3, 18, 0))

    def test_clear_datetime_uses_midnight(self) -> None:
        result = _to_msp_clear_datetime(_MSP_CLEAR_DATE)
        self.assertEqual(result, datetime(1989, 1, 1, 0, 0))
