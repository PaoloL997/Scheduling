"""Tests for operational problem detection on activity save."""

from datetime import date

from django.test import TestCase

from ui.models import (
    Attivita,
    ConfigurazioneTesta,
    ProblemaAttivita,
    RisorsaAttivita,
)
from ui.services.activity_inventory import get_testa_min_requirements
from ui.services.problem_detection import detect_and_save_problems
from ui.tests.helpers import write_user


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

    def test_tp60_custom_drops_consumable_minimums(self) -> None:
        """TP60 Custom keeps testa/generatore minimums and frees consumables."""
        ConfigurazioneTesta.objects.create(
            modello_testa="IRIS",
            min_mandrini=2,
            min_tastatori=1,
            min_maschere=3,
            min_generatori=2,
        )
        self.assertEqual(
            get_testa_min_requirements("IRIS", tp60_custom=True),
            {
                "teste": 1,
                "mandrini": 0,
                "tastatori": 0,
                "maschere": 0,
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

    def test_full_selection_auto_resolves_stale_shortfall(self) -> None:
        detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": ["M1"],
                "tastatori": ["TA1"],
                "maschere": ["MA1", "MA2"],
                "generatori": ["G1"],
            },
        )
        self.assertEqual(
            ProblemaAttivita.objects.filter(
                attivita=self.att, tipo=ProblemaAttivita.TIPO_ACQUISTO, risolto=False
            ).count(),
            1,
        )

        detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": ["M1", "M2"],
                "tastatori": ["TA1"],
                "maschere": ["MA1", "MA2"],
                "generatori": ["G1"],
            },
        )
        open_acq = ProblemaAttivita.objects.filter(
            attivita=self.att, tipo=ProblemaAttivita.TIPO_ACQUISTO, risolto=False
        )
        self.assertFalse(open_acq.exists())
        closed = ProblemaAttivita.objects.get(
            attivita=self.att, tipo=ProblemaAttivita.TIPO_ACQUISTO, risolto=True
        )
        self.assertIn("non piu verificata", closed.note_risoluzione.lower())

    def test_shortfall_description_updates_when_selection_changes(self) -> None:
        detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": [],
                "tastatori": ["TA1"],
                "maschere": ["MA1", "MA2"],
                "generatori": ["G1"],
            },
        )
        problema = ProblemaAttivita.objects.get(
            attivita=self.att, risorsa_tipo="mandrino", risolto=False
        )
        self.assertIn("0/2", problema.descrizione)

        detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": ["M1"],
                "tastatori": ["TA1"],
                "maschere": ["MA1", "MA2"],
                "generatori": ["G1"],
            },
        )
        problema.refresh_from_db()
        self.assertFalse(problema.risolto)
        self.assertIn("1/2", problema.descrizione)

    def test_shortfall_reappears_after_auto_resolve(self) -> None:
        full = {
            "teste": ["T1"],
            "mandrini": ["M1", "M2"],
            "tastatori": ["TA1"],
            "maschere": ["MA1", "MA2"],
            "generatori": ["G1"],
        }
        short = {
            "teste": ["T1"],
            "mandrini": ["M1"],
            "tastatori": ["TA1"],
            "maschere": ["MA1", "MA2"],
            "generatori": ["G1"],
        }
        detect_and_save_problems(self.att, short)
        detect_and_save_problems(self.att, full)
        created = detect_and_save_problems(self.att, short)
        self.assertEqual(created, 1)
        self.assertEqual(
            ProblemaAttivita.objects.filter(
                attivita=self.att,
                tipo=ProblemaAttivita.TIPO_ACQUISTO,
                risolto=False,
            ).count(),
            1,
        )


class DetectTp60CustomShortfallTest(TestCase):
    """TP60 Custom activities may skip consumables without raising problems."""

    def setUp(self) -> None:
        from ui.models import TestaItem

        ConfigurazioneTesta.objects.create(
            modello_testa="TP60",
            min_mandrini=2,
            min_maschere=2,
        )
        TestaItem.objects.create(uid="T1", model="TP60", plant="Valbrembo")
        TestaItem.objects.create(uid="T2", model="TP60", plant="Osio")
        self.att = Attivita.objects.create(
            commessa="C900",
            item=1,
            activity="Saldatura",
            modello_testa="TP60",
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
            tp60_custom=True,
        )

    def test_deselected_consumables_create_no_problem(self) -> None:
        count = detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": [],
                "tastatori": [],
                "maschere": [],
                "generatori": ["G1"],
            },
        )
        self.assertEqual(count, 0)
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.att, tipo=ProblemaAttivita.TIPO_ACQUISTO
            ).exists()
        )

    def test_missing_testa_and_generatore_are_still_flagged(self) -> None:
        detect_and_save_problems(
            self.att,
            {
                "teste": [],
                "mandrini": [],
                "tastatori": [],
                "maschere": [],
                "generatori": [],
            },
        )
        flagged = set(
            ProblemaAttivita.objects.filter(
                attivita=self.att, tipo=ProblemaAttivita.TIPO_ACQUISTO
            ).values_list("risorsa_tipo", flat=True)
        )
        self.assertEqual(flagged, {"testa", "generatore"})

    def test_spostamento_still_detected_for_selected_resources(self) -> None:
        detect_and_save_problems(
            self.att,
            {
                "teste": ["T2"],
                "mandrini": [],
                "tastatori": [],
                "maschere": [],
                "generatori": ["G1"],
            },
        )
        problema = ProblemaAttivita.objects.get(
            attivita=self.att, tipo=ProblemaAttivita.TIPO_SPOSTAMENTO
        )
        self.assertEqual(problema.risorsa_uid, "T2")

    def test_enabling_flag_auto_resolves_existing_shortfall(self) -> None:
        """A shortfall opened before the flag is closed once it is set."""
        self.att.tp60_custom = False
        self.att.save(update_fields=["tp60_custom"])
        empty = {
            "teste": ["T1"],
            "mandrini": [],
            "tastatori": [],
            "maschere": [],
            "generatori": ["G1"],
        }
        detect_and_save_problems(self.att, empty)
        self.assertEqual(
            ProblemaAttivita.objects.filter(
                attivita=self.att,
                tipo=ProblemaAttivita.TIPO_ACQUISTO,
                risolto=False,
            ).count(),
            3,
        )

        self.att.tp60_custom = True
        self.att.save(update_fields=["tp60_custom"])
        created = detect_and_save_problems(self.att, empty)

        self.assertEqual(created, 0)
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.att,
                tipo=ProblemaAttivita.TIPO_ACQUISTO,
                risolto=False,
            ).exists()
        )

    def test_normal_activity_still_flags_missing_consumables(self) -> None:
        att = Attivita.objects.create(
            commessa="C901",
            item=1,
            activity="Saldatura",
            modello_testa="TP60",
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
        )
        count = detect_and_save_problems(
            att,
            {
                "teste": ["T1"],
                "mandrini": [],
                "tastatori": [],
                "maschere": [],
                "generatori": ["G1"],
            },
        )
        self.assertEqual(count, 3)


class DetectSpostamentoProblemsTest(TestCase):
    """detect_and_save_problems flags and clears wrong-plant assignments."""

    def setUp(self) -> None:
        from ui.models import TestaItem

        TestaItem.objects.create(uid="T1", model="IRIS", plant="Osio")
        self.att = Attivita.objects.create(
            commessa="C001",
            item=1,
            activity="Saldatura",
            modello_testa="IRIS",
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
        )
        self.risorse = {
            "teste": ["T1"],
            "mandrini": ["M1"],
            "tastatori": ["TA1"],
            "maschere": ["MA1"],
            "generatori": ["G1"],
        }

    def test_wrong_plant_creates_spostamento(self) -> None:
        count = detect_and_save_problems(self.att, self.risorse)
        self.assertEqual(count, 1)
        problema = ProblemaAttivita.objects.get(
            attivita=self.att, tipo=ProblemaAttivita.TIPO_SPOSTAMENTO
        )
        self.assertEqual(problema.risorsa_uid, "T1")
        self.assertIn("Osio", problema.descrizione)

    def test_aligned_plant_auto_resolves_spostamento(self) -> None:
        detect_and_save_problems(self.att, self.risorse)
        self.att.stabilimento = "Osio"
        self.att.save(update_fields=["stabilimento"])
        detect_and_save_problems(self.att, self.risorse)
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.att,
                tipo=ProblemaAttivita.TIPO_SPOSTAMENTO,
                risolto=False,
            ).exists()
        )
        closed = ProblemaAttivita.objects.get(
            attivita=self.att, tipo=ProblemaAttivita.TIPO_SPOSTAMENTO
        )
        self.assertIn("spostata", closed.note_risoluzione.lower())

    def test_inventory_plant_change_auto_resolves_spostamento(self) -> None:
        from ui.models import TestaItem

        detect_and_save_problems(self.att, self.risorse)
        TestaItem.objects.filter(uid="T1").update(plant="Valbrembo")
        detect_and_save_problems(self.att, self.risorse)
        closed = ProblemaAttivita.objects.get(
            attivita=self.att, tipo=ProblemaAttivita.TIPO_SPOSTAMENTO
        )
        self.assertTrue(closed.risolto)
        self.assertIn("spostata", closed.note_risoluzione.lower())

    def test_patch_inventory_plant_closes_spostamento(self) -> None:
        import json

        from django.test import Client
        from django.urls import reverse

        detect_and_save_problems(self.att, self.risorse)
        RisorsaAttivita.objects.create(attivita=self.att, tipo="testa", risorsa_id="T1")
        self.assertTrue(
            ProblemaAttivita.objects.filter(
                attivita=self.att,
                tipo=ProblemaAttivita.TIPO_SPOSTAMENTO,
                risolto=False,
            ).exists()
        )

        client = Client()
        client.force_login(write_user())
        resp = client.patch(
            reverse(
                "api_inventario_detail",
                kwargs={"table": "Testa", "uid": "T1"},
            ),
            data=json.dumps({"plant": "Valbrembo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.att,
                tipo=ProblemaAttivita.TIPO_SPOSTAMENTO,
                risolto=False,
            ).exists()
        )
        closed = ProblemaAttivita.objects.get(
            attivita=self.att, tipo=ProblemaAttivita.TIPO_SPOSTAMENTO
        )
        self.assertIn("spostata", closed.note_risoluzione.lower())

    def test_activity_plant_update_closes_spostamento(self) -> None:
        from ui.services.bridge_write import update_attivita

        detect_and_save_problems(self.att, self.risorse)
        RisorsaAttivita.objects.create(attivita=self.att, tipo="testa", risorsa_id="T1")
        update_attivita(self.att.pk, {"plant": "Osio"})
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.att,
                tipo=ProblemaAttivita.TIPO_SPOSTAMENTO,
                risolto=False,
            ).exists()
        )


class DetectForAllActivitiesRefreshTest(TestCase):
    """detect_for_all_activities reconciles stale open notes from DB resources."""

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
        for tipo, uid in (
            ("testa", "T1"),
            ("mandrino", "M1"),
            ("mandrino", "M2"),
            ("tastatore", "TA1"),
            ("maschera", "MA1"),
            ("maschera", "MA2"),
            ("generatore", "G1"),
        ):
            RisorsaAttivita.objects.create(attivita=self.att, tipo=tipo, risorsa_id=uid)
        ProblemaAttivita.objects.create(
            attivita=self.att,
            tipo=ProblemaAttivita.TIPO_ACQUISTO,
            risorsa_tipo="mandrino",
            risorsa_uid="",
            descrizione="stale shortfall",
        )

    def test_detect_for_all_closes_stale_acquisto(self) -> None:
        from ui.services.problem_detection import detect_for_all_activities

        detect_for_all_activities()
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.att,
                tipo=ProblemaAttivita.TIPO_ACQUISTO,
                risolto=False,
            ).exists()
        )


class FindResourceOverlapsTest(TestCase):
    """Delay-caused resource overlaps are computed on read, not persisted."""

    def setUp(self) -> None:
        self.late = Attivita.objects.create(
            commessa="C101",
            item=1,
            activity="Saldatura",
            stabilimento="Valbrembo",
            data_inizio=date(2026, 8, 10),
            data_fine=date(2026, 8, 19),
            actual_start=date(2026, 8, 17),
        )
        self.blocked = Attivita.objects.create(
            commessa="C205",
            item=2,
            activity="Montaggio",
            stabilimento="Valbrembo",
            data_inizio=date(2026, 8, 20),
            data_fine=date(2026, 8, 29),
        )
        for att in (self.late, self.blocked):
            RisorsaAttivita.objects.create(
                attivita=att, tipo="testa", risorsa_id="OV-T1"
            )

    def test_flags_both_activities_with_shared_resource(self) -> None:
        from ui.services.problem_detection import find_resource_overlaps

        result = find_resource_overlaps()
        self.assertIn(self.late.pk, result)
        self.assertIn(self.blocked.pk, result)

        late_item = result[self.late.pk][0]
        self.assertEqual(late_item["role"], "late")
        self.assertEqual(late_item["peer_orm_id"], self.blocked.pk)
        self.assertEqual(late_item["peer_commessa"], "C205")
        self.assertEqual(late_item["peer_item"], "2")
        self.assertEqual(late_item["peer_activity"], "Montaggio")
        self.assertEqual(late_item["overlap_start"], "2026-08-20")
        self.assertEqual(late_item["overlap_end"], "2026-08-26")
        self.assertEqual(late_item["resources"][0]["uid"], "OV-T1")

        blocked_item = result[self.blocked.pk][0]
        self.assertEqual(blocked_item["role"], "blocked")
        self.assertEqual(blocked_item["peer_orm_id"], self.late.pk)

    def test_no_overlap_without_actual_start(self) -> None:
        from ui.services.problem_detection import find_resource_overlaps

        self.late.actual_start = None
        self.late.save()
        self.assertEqual(find_resource_overlaps(), {})

    def test_no_overlap_without_shared_resource(self) -> None:
        from ui.services.problem_detection import find_resource_overlaps

        RisorsaAttivita.objects.filter(attivita=self.blocked).update(risorsa_id="OV-T9")
        self.assertEqual(find_resource_overlaps(), {})

    def test_does_not_persist_problema_rows(self) -> None:
        from ui.services.problem_detection import find_resource_overlaps

        find_resource_overlaps()
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE
            ).exists()
        )
