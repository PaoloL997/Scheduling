"""Tests for activity resource updates and availability API."""

from __future__ import annotations

import json
from datetime import date

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from ui.models import (
    Attivita,
    ConfigurazioneTesta,
    GeneratoreItem,
    MandrinoItem,
    MascheraItem,
    MppSyncQueue,
    RisorsaAttivita,
    TastatoreItem,
    TestaItem,
)
from ui.services.activity_inventory import get_available_resources
from ui.services.bridge_write import BridgeWriteError, update_attivita
from ui.views import api_tasks_update
from ui.views_attivita import api_get_risorse


class UpdateAttivitaRisorseTest(TestCase):
    def setUp(self) -> None:
        self.att = Attivita.objects.create(
            commessa="C100",
            item=1,
            activity="A1",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            stabilimento="Valbrembo",
        )
        RisorsaAttivita.objects.create(
            attivita=self.att, tipo="testa", risorsa_id="T001"
        )

    def test_update_risorse_replaces_assignments(self) -> None:
        result = update_attivita(
            self.att.pk,
            {
                "risorse": {
                    "teste": ["T002"],
                    "mandrini": [],
                    "tastatori": [],
                    "maschere": [],
                    "generatori": [],
                }
            },
        )
        rows = list(
            RisorsaAttivita.objects.filter(attivita_id=self.att.pk).order_by(
                "risorsa_id"
            )
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].tipo, "testa")
        self.assertEqual(rows[0].risorsa_id, "T002")
        self.assertIn("resources_by_tipo", result)
        self.assertEqual(result["resources_by_tipo"]["testa"], ["T002"])

    def test_update_risorse_enqueues_resources_for_mpp_writeback(self) -> None:
        update_attivita(
            self.att.pk,
            {
                "risorse": {
                    "teste": ["T002"],
                    "mandrini": ["M1"],
                    "tastatori": [],
                    "maschere": [],
                    "generatori": [],
                }
            },
        )
        entry = MppSyncQueue.objects.get(operation="update_task")
        payload = json.loads(entry.payload_json)
        self.assertEqual(payload["resources"], ["T002", "M1"])

    def test_update_predecessors_enqueued_for_mpp_writeback(self) -> None:
        pred = Attivita.objects.create(
            commessa="C100",
            item=2,
            activity="Prep",
            data_inizio=date(2026, 5, 1),
            data_fine=date(2026, 5, 3),
        )
        update_attivita(
            self.att.pk,
            {"predecessors": [pred.activity_id]},
        )
        self.att.refresh_from_db()
        self.assertEqual(self.att.predecessors, pred.activity_id)
        entry = MppSyncQueue.objects.get(operation="update_task")
        payload = json.loads(entry.payload_json)
        self.assertEqual(payload["predecessors"], pred.activity_id)

    def test_update_risorse_conflict_raises(self) -> None:
        other = Attivita.objects.create(
            commessa="C200",
            item=1,
            activity="A1",
            data_inizio=date(2026, 6, 2),
            data_fine=date(2026, 6, 4),
        )
        RisorsaAttivita.objects.create(
            attivita=other, tipo="testa", risorsa_id="T_BUSY"
        )
        with self.assertRaises(BridgeWriteError) as ctx:
            update_attivita(
                self.att.pk,
                {
                    "risorse": {
                        "teste": ["T_BUSY"],
                        "mandrini": [],
                        "tastatori": [],
                        "maschere": [],
                        "generatori": [],
                    }
                },
            )
        self.assertEqual(ctx.exception.status_code, 409)


class ApiGetRisorseExcludeTest(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user("ris", password="pass")
        TestaItem.objects.create(uid="T001", model="HT200", plant="Valbrembo")
        TestaItem.objects.create(uid="T002", model="HT200", plant="Valbrembo")
        self.att = Attivita.objects.create(
            commessa="C100",
            item=1,
            activity="A1",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            stabilimento="Valbrembo",
            modello_testa="HT200",
        )
        RisorsaAttivita.objects.create(
            attivita=self.att, tipo="testa", risorsa_id="T001"
        )
        other = Attivita.objects.create(
            commessa="C200",
            item=1,
            activity="A1",
            data_inizio=date(2026, 6, 2),
            data_fine=date(2026, 6, 4),
            stabilimento="Valbrembo",
            modello_testa="HT200",
        )
        RisorsaAttivita.objects.create(attivita=other, tipo="testa", risorsa_id="T002")

    def test_exclude_activity_id_keeps_own_resource_available(self) -> None:
        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            modello_testa="HT200",
            exclude_activity_id=self.att.pk,
        )
        uids = {r["uid"] for r in result["teste"]}
        self.assertIn("T001", uids)
        self.assertNotIn("T002", uids)

    def test_without_exclude_hides_booked_resource(self) -> None:
        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            modello_testa="HT200",
        )
        uids = {r["uid"] for r in result["teste"]}
        self.assertNotIn("T001", uids)
        self.assertNotIn("T002", uids)

    def test_api_passes_exclude_activity_id(self) -> None:
        request = self.factory.get(
            "/api/attivita/risorse/",
            {
                "stabilimento": "Valbrembo",
                "data_inizio": "2026-06-01",
                "data_fine": "2026-06-05",
                "modello_testa": "HT200",
                "exclude_activity_id": str(self.att.pk),
            },
        )
        request.user = self.user
        resp = api_get_risorse(request)
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])
        uids = {r["uid"] for r in data["teste"]}
        self.assertIn("T001", uids)


class StrictRequirementsTest(TestCase):
    def setUp(self) -> None:
        TestaItem.objects.create(uid="T001", model="HT200", plant="Valbrembo")
        TestaItem.objects.create(uid="T002", model="IRIS", plant="Valbrembo")
        MandrinoItem.objects.create(uid="M_OK", diameter="25", plant="Valbrembo")
        MandrinoItem.objects.create(uid="M_BAD", diameter="50", plant="Valbrembo")

    def test_strict_filters_mandrini_by_bore_range(self) -> None:
        loose = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
        )
        self.assertEqual(len(loose["mandrini"]), 2)

        strict = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            modello_testa="HT200",
            bore_min=20.0,
            bore_max=30.0,
            strict_requirements=True,
        )
        mandrini_uids = {r["uid"] for r in strict["mandrini"]}
        self.assertIn("M_OK", mandrini_uids)
        self.assertNotIn("M_BAD", mandrini_uids)
        self.assertTrue(all(r.get("model") == "HT200" for r in strict["teste"]))

    def test_strict_without_modello_returns_no_teste(self) -> None:
        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            bore_min=20.0,
            bore_max=30.0,
            strict_requirements=True,
        )
        self.assertEqual(result["teste"], [])

    def test_relax_requirements_returns_all_free_resources(self) -> None:
        MandrinoItem.objects.create(uid="M_OTHER", diameter="80", plant="Dalmine")
        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            modello_testa="HT200",
            bore_min=20.0,
            bore_max=30.0,
            strict_requirements=True,
            relax_requirements=True,
        )
        teste_uids = {r["uid"] for r in result["teste"]}
        mandrini_uids = {r["uid"] for r in result["mandrini"]}
        self.assertIn("T001", teste_uids)
        self.assertIn("T002", teste_uids)
        self.assertIn("M_OK", mandrini_uids)
        self.assertIn("M_BAD", mandrini_uids)
        self.assertIn("M_OTHER", mandrini_uids)
        other = next(r for r in result["mandrini"] if r["uid"] == "M_OTHER")
        self.assertEqual(other.get("stab_esterno"), "Dalmine")

    def test_api_relax_requirements_overrides_strict_from_activity(self) -> None:
        factory = RequestFactory()
        user = User.objects.create_user("relax", password="pass")
        att = Attivita.objects.create(
            commessa="C101",
            item=1,
            activity="A1",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            stabilimento="Valbrembo",
            modello_testa="HT200",
            bore_min=20,
            bore_max=30,
        )
        request = factory.get(
            "/api/attivita/risorse/",
            {
                "stabilimento": "Valbrembo",
                "data_inizio": "2026-06-01",
                "data_fine": "2026-06-05",
                "exclude_activity_id": str(att.pk),
                "relax_requirements": "1",
            },
        )
        request.user = user
        resp = api_get_risorse(request)
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])
        mandrini_uids = {r["uid"] for r in data["mandrini"]}
        self.assertIn("M_OK", mandrini_uids)
        self.assertIn("M_BAD", mandrini_uids)

    def test_api_edit_loads_requirements_from_activity(self) -> None:
        factory = RequestFactory()
        user = User.objects.create_user("strict", password="pass")
        att = Attivita.objects.create(
            commessa="C100",
            item=1,
            activity="A1",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            stabilimento="Valbrembo",
            modello_testa="HT200",
            bore_min=20,
            bore_max=30,
        )
        request = factory.get(
            "/api/attivita/risorse/",
            {
                "stabilimento": "Valbrembo",
                "data_inizio": "2026-06-01",
                "data_fine": "2026-06-05",
                "exclude_activity_id": str(att.pk),
            },
        )
        request.user = user
        resp = api_get_risorse(request)
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])
        self.assertTrue(all(r.get("model") == "HT200" for r in data["teste"]))
        mandrini_uids = {r["uid"] for r in data["mandrini"]}
        self.assertIn("M_OK", mandrini_uids)
        self.assertNotIn("M_BAD", mandrini_uids)


class ApiTasksUpdateRisorseTest(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user("web", password="pass")
        self.att = Attivita.objects.create(
            commessa="C100",
            item=1,
            activity="A1",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
        )

    def test_api_tasks_update_accepts_risorse(self) -> None:
        request = self.factory.put(
            "/api/tasks/update/",
            data=json.dumps(
                {
                    "orm_id": self.att.pk,
                    "risorse": {
                        "teste": ["T010"],
                        "mandrini": [],
                        "tastatori": [],
                        "maschere": [],
                        "generatori": [],
                    },
                }
            ),
            content_type="application/json",
        )
        request.user = self.user
        resp = api_tasks_update(request)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])
        self.assertEqual(data["resources_by_tipo"]["testa"], ["T010"])


class PreselectDiversificationTest(TestCase):
    """Idle-first pre-selection among resources free in the candidate window."""

    def setUp(self) -> None:
        TestaItem.objects.create(uid="T001", model="HT200", plant="Valbrembo")
        TestaItem.objects.create(uid="T002", model="HT200", plant="Valbrembo")
        MandrinoItem.objects.create(uid="M_BUSY", diameter="25", plant="Valbrembo")
        MandrinoItem.objects.create(uid="M_IDLE_NEAR", diameter="26", plant="Valbrembo")
        MandrinoItem.objects.create(uid="M_IDLE_FAR", diameter="40", plant="Valbrembo")

    def _planned(self, commessa: str, start: date, finish: date, **kwargs) -> Attivita:
        return Attivita.objects.create(
            commessa=commessa,
            item=1,
            activity="A1",
            data_inizio=start,
            data_fine=finish,
            stabilimento="Valbrembo",
            **kwargs,
        )

    def test_prefers_idle_testa_over_one_assigned_elsewhere(self) -> None:
        other = self._planned("C200", date(2026, 3, 1), date(2026, 3, 10))
        RisorsaAttivita.objects.create(attivita=other, tipo="testa", risorsa_id="T001")

        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            modello_testa="HT200",
        )
        self.assertEqual(result["pre_testa"], {"T002"})

    def test_completed_assignment_does_not_penalize(self) -> None:
        done = self._planned(
            "C200",
            date(2026, 3, 1),
            date(2026, 3, 10),
            actual_start=date(2026, 3, 1),
            actual_finish=date(2026, 3, 8),
        )
        open_att = self._planned("C300", date(2026, 4, 1), date(2026, 4, 10))
        RisorsaAttivita.objects.create(attivita=done, tipo="testa", risorsa_id="T002")
        RisorsaAttivita.objects.create(
            attivita=open_att, tipo="testa", risorsa_id="T001"
        )

        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            modello_testa="HT200",
        )
        self.assertEqual(result["pre_testa"], {"T002"})

    def test_falls_back_to_assigned_when_all_busy(self) -> None:
        a = self._planned("C200", date(2026, 3, 1), date(2026, 3, 10))
        b = self._planned("C300", date(2026, 4, 1), date(2026, 4, 10))
        RisorsaAttivita.objects.create(attivita=a, tipo="testa", risorsa_id="T001")
        RisorsaAttivita.objects.create(attivita=b, tipo="testa", risorsa_id="T002")

        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            modello_testa="HT200",
        )
        self.assertEqual(len(result["pre_testa"]), 1)
        self.assertTrue(result["pre_testa"].issubset({"T001", "T002"}))

    def test_idle_mandrino_closest_to_bore_mid(self) -> None:
        other = self._planned("C200", date(2026, 3, 1), date(2026, 3, 10))
        RisorsaAttivita.objects.create(
            attivita=other, tipo="mandrino", risorsa_id="M_BUSY"
        )

        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            bore_min=20.0,
            bore_max=50.0,
            bore_mid=25.0,
        )
        self.assertEqual(result["pre_mandrini"], {"M_IDLE_NEAR"})
        self.assertNotIn("M_BUSY", result["pre_mandrini"])

    def test_fills_minimum_with_idle_then_busy(self) -> None:
        ConfigurazioneTesta.objects.create(modello_testa="HT200", min_mandrini=2)
        other = self._planned("C200", date(2026, 3, 1), date(2026, 3, 10))
        RisorsaAttivita.objects.create(
            attivita=other, tipo="mandrino", risorsa_id="M_BUSY"
        )

        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            modello_testa="HT200",
            bore_min=20.0,
            bore_max=50.0,
            bore_mid=25.0,
        )
        self.assertEqual(result["pre_mandrini"], {"M_IDLE_NEAR", "M_IDLE_FAR"})

        ConfigurazioneTesta.objects.filter(modello_testa="HT200").update(min_mandrini=3)
        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            modello_testa="HT200",
            bore_min=20.0,
            bore_max=50.0,
            bore_mid=25.0,
        )
        self.assertEqual(
            result["pre_mandrini"], {"M_IDLE_NEAR", "M_IDLE_FAR", "M_BUSY"}
        )

    def test_in_progress_assignment_counts_as_busy(self) -> None:
        working = self._planned(
            "C200",
            date(2026, 3, 1),
            date(2026, 3, 10),
            actual_start=date(2026, 3, 1),
        )
        RisorsaAttivita.objects.create(
            attivita=working, tipo="testa", risorsa_id="T001"
        )

        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            modello_testa="HT200",
        )
        self.assertEqual(result["pre_testa"], {"T002"})

    def test_exclude_activity_id_treats_own_assignment_as_idle(self) -> None:
        current = self._planned("C100", date(2026, 6, 1), date(2026, 6, 5))
        other = self._planned("C200", date(2026, 3, 1), date(2026, 3, 10))
        RisorsaAttivita.objects.create(
            attivita=current, tipo="testa", risorsa_id="T001"
        )
        RisorsaAttivita.objects.create(attivita=other, tipo="testa", risorsa_id="T002")

        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            modello_testa="HT200",
            exclude_activity_id=current.pk,
        )
        self.assertEqual(result["pre_testa"], {"T001"})

    def test_idle_first_on_tastatori_maschere_generatori(self) -> None:
        TastatoreItem.objects.create(uid="TA_BUSY", diameter="25", plant="Valbrembo")
        TastatoreItem.objects.create(uid="TA_IDLE", diameter="40", plant="Valbrembo")
        MascheraItem.objects.create(uid="MA001", pitch="50", plant="Valbrembo")
        MascheraItem.objects.create(uid="MA002", pitch="50", plant="Valbrembo")
        GeneratoreItem.objects.create(uid="G001", model="GEN-A", plant="Valbrembo")
        GeneratoreItem.objects.create(uid="G002", model="GEN-A", plant="Valbrembo")

        other = self._planned("C200", date(2026, 3, 1), date(2026, 3, 10))
        RisorsaAttivita.objects.create(
            attivita=other, tipo="tastatore", risorsa_id="TA_BUSY"
        )
        RisorsaAttivita.objects.create(
            attivita=other, tipo="maschera", risorsa_id="MA001"
        )
        RisorsaAttivita.objects.create(
            attivita=other, tipo="generatore", risorsa_id="G001"
        )

        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            bore_min=20.0,
            bore_max=50.0,
            bore_mid=25.0,
            tube_pitch=50.0,
        )
        self.assertEqual(result["pre_tastatori"], {"TA_IDLE"})
        self.assertEqual(result["pre_maschere"], {"MA002"})
        self.assertEqual(result["pre_generatori"], {"G002"})
