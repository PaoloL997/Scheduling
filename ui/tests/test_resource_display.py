"""Tests for activity resource display formatting."""

from django.test import SimpleTestCase

from ui.services.resource_display import (
    format_activity_resources,
    format_activity_resources_html,
)


def _r(tipo: str, risorsa_id: str) -> dict:
    return {"tipo": tipo, "risorsa_id": risorsa_id}


class FormatActivityResourcesTests(SimpleTestCase):
    """Tests for :func:`format_activity_resources`."""

    def test_testa_generatore_and_accessories(self) -> None:
        result = format_activity_resources(
            [
                _r("testa", "T001"),
                _r("generatore", "G501"),
                _r("mandrino", "M12"),
                _r("tastatore", "TA3"),
            ]
        )
        self.assertEqual(result["display"], "T001 · G501 + 2 accessori")
        self.assertEqual(
            result["tooltip"],
            "Testa: T001\nGeneratore: G501\nMandrino: M12\nTastatore: TA3",
        )
        self.assertEqual(result["flat_ids"], ["T001", "G501", "M12", "TA3"])

    def test_only_accessories(self) -> None:
        result = format_activity_resources(
            [
                _r("mandrino", "M12"),
                _r("maschera", "MSK1"),
            ]
        )
        self.assertEqual(result["display"], "+ 2 accessori")
        self.assertEqual(
            result["tooltip"],
            "Mandrino: M12\nMaschera: MSK1",
        )

    def test_no_resources(self) -> None:
        result = format_activity_resources([])
        self.assertEqual(result["display"], "—")
        self.assertEqual(result["tooltip"], "")
        self.assertEqual(result["flat_ids"], [])

    def test_multiple_teste_and_generatori(self) -> None:
        result = format_activity_resources(
            [
                _r("testa", "T001"),
                _r("testa", "T002"),
                _r("generatore", "G501"),
                _r("generatore", "G502"),
            ]
        )
        self.assertEqual(result["display"], "T001 · T002 · G501 · G502")

    def test_single_accessorio_label(self) -> None:
        result = format_activity_resources([_r("testa", "T001"), _r("mandrino", "M12")])
        self.assertEqual(result["display"], "T001 + 1 accessorio")

    def test_html_includes_chips_and_tooltip(self) -> None:
        html = format_activity_resources_html(
            [
                _r("testa", "T001"),
                _r("generatore", "G501"),
                _r("mandrino", "M12"),
            ]
        )
        self.assertIn('class="pian-res-chip pian-res-chip--testa"', html)
        self.assertIn("T001", html)
        self.assertIn('class="pian-res-chip pian-res-chip--gen"', html)
        self.assertIn("G501", html)
        self.assertIn(
            'class="pian-res-chip pian-res-chip--acc" title="Mandrino">M12<', html
        )
        self.assertNotIn("accessorio", html)
        self.assertIn('data-tooltip="', html)
        self.assertIn("Mandrino: M12", html)

    def test_html_empty(self) -> None:
        self.assertEqual(format_activity_resources_html([]), "—")
