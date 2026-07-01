"""Unit tests for task-name parsing in :mod:`src.data.import`."""

import importlib

from django.test import SimpleTestCase

_data_import = importlib.import_module("src.data.import")
_parse_task_name = _data_import._parse_task_name
_wps_parser = importlib.import_module("ui.services.wps_parser")
parse_wps_name = _wps_parser.parse_wps_name


class ParseTaskNameTests(SimpleTestCase):
    """Tests for :func:`_parse_task_name`."""

    def test_canonical_underscore_three_parts(self):
        """Full ``commessa_item_activity`` format with underscores."""
        self.assertEqual(
            _parse_task_name("25033_01_qualifica"), ("25033", "01", "qualifica")
        )

    def test_canonical_underscore_two_parts(self):
        """``commessa_item`` format without activity."""
        self.assertEqual(_parse_task_name("25033_02"), ("25033", "02", ""))

    def test_legacy_hyphen_three_parts(self):
        """Legacy ``commessa-item-activity`` format with hyphens."""
        self.assertEqual(
            _parse_task_name("25033-01-qualifica"), ("25033", "01", "qualifica")
        )

    def test_legacy_hyphen_two_parts(self):
        """Legacy ``commessa-item`` format without activity."""
        self.assertEqual(_parse_task_name("25033-01"), ("25033", "01", ""))

    def test_commessa_only(self):
        """Single-segment name returns item and activity as empty strings."""
        self.assertEqual(_parse_task_name("25033"), ("25033", "", ""))

    def test_activity_with_underscores_preserved(self):
        """Activity labels that themselves contain underscores are kept intact."""
        self.assertEqual(
            _parse_task_name("25033_01_saldatura_orbitale"),
            ("25033", "01", "saldatura_orbitale"),
        )

    def test_underscore_takes_priority_over_hyphen(self):
        """When both separators are present, underscore wins."""
        self.assertEqual(
            _parse_task_name("AB_01_sal-d"),
            ("AB", "01", "sal-d"),
        )


class ParseWpsNameTests(SimpleTestCase):
    """Tests for WPS filename parsing."""

    def test_full_model_commessa_item_format(self):
        self.assertEqual(
            parse_wps_name("HT200_25033_02_qualifica.pdf"),
            {
                "modello_testa": "HT200",
                "commessa": "25033",
                "item": 2,
                "attivita": "",
            },
        )

    def test_item_and_activity_suffix_ignored(self):
        self.assertEqual(
            parse_wps_name("HT200_25033_Saldatura.pdf"),
            {
                "modello_testa": "HT200",
                "commessa": "25033",
                "item": 1,
                "attivita": "",
            },
        )

    def test_item_is_optional(self):
        self.assertEqual(
            parse_wps_name("HT200_25033.pdf"),
            {
                "modello_testa": "HT200",
                "commessa": "25033",
                "item": 1,
                "attivita": "",
            },
        )
