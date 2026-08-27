"""Tests for testa/generatore maintenance flag and reassignment."""

from __future__ import annotations

import importlib
import json
from datetime import date
from unittest.mock import MagicMock

from django.test import Client, TestCase
from django.urls import reverse

from src.data.models import Resource
from ui.models import (
    Attivita,
    ConfigurazioneTesta,
    GeneratoreItem,
    MandrinoItem,
    MppSyncQueue,
    ProblemaAttivita,
    RisorsaAttivita,
    TestaItem,
)
from ui.services.activity_inventory import get_available_resources
from ui.services.inventory import patch_resource
from ui.services.optimization_problem import _fetch_inventory, reservations_for_excluded
from ui.services.writeback import _apply_add_resource, _apply_update_resource

_data_import = importlib.import_module("src.data.import")


class MaintenanceApiTest(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        GeneratoreItem.objects.create(
            uid="G100", model="g1", plant="Valbrembo", notes="originale"
        )
        TestaItem.objects.create(uid="T100", model="TP60", plant="Valbrembo")
        MandrinoItem.objects.create(uid="M100", diameter="15", plant="Valbrembo")

    def test_patch_sets_flag_and_enqueues_mpp(self) -> None:
        resp = self.client.patch(
            reverse(
                "api_inventario_detail",
                kwargs={"table": "Generatore", "uid": "G100"},
            ),
            data=json.dumps({"in_manutenzione": True}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        item = GeneratoreItem.objects.get(uid="G100")
        self.assertTrue(item.in_manutenzione)
        entry = MppSyncQueue.objects.get(operation="update_resource")
        payload = json.loads(entry.payload_json)
        self.assertEqual(payload["original_uid"], "G100")
        self.assertTrue(payload["in_manutenzione"])
        self.assertNotIn("plant", payload)

    def test_patch_rejected_on_mandrino(self) -> None:
        resp = self.client.patch(
            reverse(
                "api_inventario_detail",
                kwargs={"table": "Mandrino", "uid": "M100"},
            ),
            data=json.dumps({"in_manutenzione": True}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Testa e Generatore", resp.json()["error"])


class MaintenanceAvailabilityTest(TestCase):
    def test_excluded_from_available_resources(self) -> None:
        TestaItem.objects.create(
            uid="T1", model="HT200", plant="Valbrembo", in_manutenzione=True
        )
        TestaItem.objects.create(uid="T2", model="HT200", plant="Valbrembo")
        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            modello_testa="HT200",
        )
        uids = {row["uid"] for row in result["teste"]}
        self.assertNotIn("T1", uids)
        self.assertIn("T2", uids)

    def test_excluded_from_optimization_inventory(self) -> None:
        TestaItem.objects.create(
            uid="T1", model="HT200", plant="Valbrembo", in_manutenzione=True
        )
        TestaItem.objects.create(uid="T2", model="HT200", plant="Valbrembo")
        GeneratoreItem.objects.create(
            uid="G1", model="P6", plant="Valbrembo", in_manutenzione=True
        )
        GeneratoreItem.objects.create(uid="G2", model="P6", plant="Valbrembo")
        ids = {res.resource_id for res in _fetch_inventory()}
        self.assertNotIn("testa:T1", ids)
        self.assertIn("testa:T2", ids)
        self.assertNotIn("generatore:G1", ids)
        self.assertIn("generatore:G2", ids)

    def test_reservations_skip_maintenance_ids(self) -> None:
        TestaItem.objects.create(
            uid="OV-R1", model="TP60", plant="Valbrembo", in_manutenzione=True
        )
        planned = Attivita.objects.create(
            commessa="CX",
            item=1,
            activity="Foratura",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2027, 5, 1),
            data_fine=date(2027, 5, 10),
        )
        RisorsaAttivita.objects.create(
            attivita=planned, tipo="testa", risorsa_id="OV-R1"
        )
        reservations = reservations_for_excluded(date(2027, 1, 1), set())
        self.assertFalse(any(r.resource_id == "testa:OV-R1" for r in reservations))


class MaintenanceReassignTest(TestCase):
    def setUp(self) -> None:
        ConfigurazioneTesta.objects.create(
            modello_testa="TP60",
            min_mandrini=0,
            min_tastatori=0,
            min_maschere=0,
            min_generatori=0,
        )
        self.head = TestaItem.objects.create(
            uid="OV-T1", model="TP60", plant="Valbrembo"
        )
        self.open_att = Attivita.objects.create(
            commessa="C205",
            item=1,
            activity="Montaggio",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 20),
            data_fine=date(2026, 8, 29),
        )
        RisorsaAttivita.objects.create(
            attivita=self.open_att, tipo="testa", risorsa_id="OV-T1"
        )

    def test_swaps_compatible_spare(self) -> None:
        TestaItem.objects.create(uid="OV-T2", model="TP60", plant="Valbrembo")
        patch_resource("Testa", "OV-T1", {"in_manutenzione": True})
        self.assertTrue(TestaItem.objects.get(uid="OV-T1").in_manutenzione)
        self.assertEqual(
            list(
                RisorsaAttivita.objects.filter(attivita=self.open_att).values_list(
                    "risorsa_id", flat=True
                )
            ),
            ["OV-T2"],
        )
        note = ProblemaAttivita.objects.get(
            attivita=self.open_att,
            tipo=ProblemaAttivita.TIPO_MANUTENZIONE,
            risolto=True,
        )
        self.assertIn("OV-T1", note.descrizione)
        self.assertIn("OV-T2", note.descrizione)
        self.assertIn("manutenzione", note.descrizione.lower())
        self.assertIn("Sostituzione automatica", note.note_risoluzione)

    def test_removes_and_creates_acquisto_without_spare(self) -> None:
        patch_resource("Testa", "OV-T1", {"in_manutenzione": True})
        self.assertFalse(
            RisorsaAttivita.objects.filter(
                attivita=self.open_att, tipo="testa", risorsa_id="OV-T1"
            ).exists()
        )
        problem = ProblemaAttivita.objects.get(
            attivita=self.open_att,
            tipo=ProblemaAttivita.TIPO_ACQUISTO,
            risolto=False,
        )
        self.assertIn("Testa", problem.descrizione)
        self.assertIn("0/1", problem.descrizione)

    def test_completed_activity_is_untouched(self) -> None:
        TestaItem.objects.create(uid="OV-T2", model="TP60", plant="Valbrembo")
        done = Attivita.objects.create(
            commessa="C300",
            item=1,
            activity="Chiusa",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 7, 1),
            data_fine=date(2026, 7, 10),
            actual_start=date(2026, 7, 1),
            actual_finish=date(2026, 7, 10),
        )
        RisorsaAttivita.objects.create(attivita=done, tipo="testa", risorsa_id="OV-T1")
        patch_resource("Testa", "OV-T1", {"in_manutenzione": True})
        self.assertEqual(RisorsaAttivita.objects.get(attivita=done).risorsa_id, "OV-T1")
        self.assertEqual(
            RisorsaAttivita.objects.get(attivita=self.open_att).risorsa_id, "OV-T2"
        )


class MaintenanceMppMappingTest(TestCase):
    def test_resource_converters_copy_flag(self) -> None:
        resource = Resource(
            name="T1",
            measure="TP60",
            plant="Valbrembo",
            category="Testa",
            in_manutenzione=True,
        )
        self.assertTrue(_data_import._resource_to_testa(resource).in_manutenzione)
        gen = Resource(
            name="G1",
            measure="P6",
            plant="Valbrembo",
            category="Generatore",
            in_manutenzione=True,
        )
        self.assertTrue(_data_import._resource_to_generatore(gen).in_manutenzione)

    def test_add_resource_writes_flag1(self) -> None:
        wrapper = MagicMock()
        wrapper._find_resource_or_none.return_value = None
        _apply_add_resource(
            wrapper,
            {
                "uid": "T1",
                "table": "Testa",
                "measure": "TP60",
                "plant": "Valbrembo",
                "in_manutenzione": True,
            },
        )
        wrapper.add_resource.assert_called_once()
        added = wrapper.add_resource.call_args[0][0]
        self.assertTrue(added.in_manutenzione)

    def test_update_resource_writes_flag1(self) -> None:
        wrapper = MagicMock()
        _apply_update_resource(
            wrapper,
            {
                "original_uid": "T1",
                "table": "Testa",
                "in_manutenzione": True,
            },
        )
        kwargs = wrapper.update_resource.call_args.kwargs
        self.assertTrue(kwargs["in_manutenzione"])
        self.assertIsNone(kwargs["plant"])
