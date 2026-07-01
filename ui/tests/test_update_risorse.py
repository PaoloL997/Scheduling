"""Tests for activity resource updates and availability API."""

from __future__ import annotations

import json
from datetime import date

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from ui.models import Attivita, MandrinoItem, RisorsaAttivita, TestaItem
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
