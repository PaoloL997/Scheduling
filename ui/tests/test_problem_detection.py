"""Tests for operational problem detection on activity save."""

from datetime import date

from django.test import TestCase

from ui.models import Attivita, ConfigurazioneTesta, ProblemaAttivita
from ui.services.activity_inventory import get_testa_min_requirements
from ui.services.problem_detection import detect_and_save_problems


class GetTestaMinRequirementsTest(TestCase):
    """get_testa_min_requirements reads ConfigurazioneTesta minimum counts."""

    def test_defaults_when_no_configuration(self) -> None:
        self.assertEqual(
            get_testa_min_requirements("UNKNOWN"),
            {
                "teste": 1,
                "mandrini": 1,
                "tastatori": 1,
                "maschere": 1,
                "generatori": 1,
            },
        )

    def test_reads_configured_minimums(self) -> None:
        ConfigurazioneTesta.objects.create(
            modello_testa="IRIS",
            min_mandrini=2,
            min_tastatori=1,
            min_maschere=3,
            min_generatori=2,
        )
        self.assertEqual(
            get_testa_min_requirements("IRIS"),
            {
                "teste": 1,
                "mandrini": 2,
                "tastatori": 1,
                "maschere": 3,
                "generatori": 2,
            },
        )


class DetectShortfallProblemsTest(TestCase):
    """detect_and_save_problems flags under-selected resource categories."""

    def setUp(self) -> None:
        ConfigurazioneTesta.objects.create(
            modello_testa="IRIS",
            min_mandrini=2,
            min_maschere=2,
        )
        self.att = Attivita.objects.create(
            commessa="C001",
            item=1,
            activity="Saldatura",
            modello_testa="IRIS",
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
        )

    def test_empty_category_creates_acquisto_problem_with_minimum(self) -> None:
        count = detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": ["M1", "M2"],
                "tastatori": ["TA1"],
                "maschere": [],
                "generatori": ["G1"],
            },
        )
        self.assertEqual(count, 1)
        problema = ProblemaAttivita.objects.get(
            attivita=self.att, risorsa_tipo="maschera"
        )
        self.assertEqual(problema.tipo, ProblemaAttivita.TIPO_ACQUISTO)
        self.assertIn("0/2", problema.descrizione)
        self.assertIn("IRIS", problema.descrizione)

    def test_partial_selection_creates_shortfall_problem(self) -> None:
        count = detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": ["M1"],
                "tastatori": ["TA1"],
                "maschere": ["MA1", "MA2"],
                "generatori": ["G1"],
            },
        )
        self.assertEqual(count, 1)
        problema = ProblemaAttivita.objects.get(
            attivita=self.att, risorsa_tipo="mandrino"
        )
        self.assertIn("1/2", problema.descrizione)
        self.assertIn("manca", problema.descrizione.lower())

    def test_full_selection_creates_no_shortfall_problems(self) -> None:
        count = detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": ["M1", "M2"],
                "tastatori": ["TA1"],
                "maschere": ["MA1", "MA2"],
                "generatori": ["G1"],
            },
        )
        self.assertEqual(count, 0)
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.att, tipo=ProblemaAttivita.TIPO_ACQUISTO
            ).exists()
        )
