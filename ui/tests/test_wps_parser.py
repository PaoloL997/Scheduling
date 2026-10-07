"""Unit tests for WPS filename parsing in :mod:`ui.services.wps_parser`."""

from django.test import SimpleTestCase

from ui.services.wps_parser import parse_wps_name


class ParseWpsNameTests(SimpleTestCase):
    """Tests for WPS filename parsing."""

    def test_full_model_commessa_item_format(self):
        """Everything after the second underscore is item/disegno."""
        self.assertEqual(
            parse_wps_name("HT200_25033_02_qualifica.pdf"),
            {
                "modello_testa": "HT200",
                "commessa": "25033",
                "item": "02_qualifica",
                "attivita": "",
            },
        )

    def test_non_numeric_item_is_kept(self):
        self.assertEqual(
            parse_wps_name("HT200_25033_Saldatura.pdf"),
            {
                "modello_testa": "HT200",
                "commessa": "25033",
                "item": "Saldatura",
                "attivita": "",
            },
        )

    def test_item_is_optional(self):
        self.assertEqual(
            parse_wps_name("HT200_25033.pdf"),
            {
                "modello_testa": "HT200",
                "commessa": "25033",
                "item": "1",
                "attivita": "",
            },
        )

    def test_numeric_item_only(self):
        self.assertEqual(
            parse_wps_name("HT200_AB123_02.pdf"),
            {
                "modello_testa": "HT200",
                "commessa": "AB123",
                "item": "02",
                "attivita": "",
            },
        )
