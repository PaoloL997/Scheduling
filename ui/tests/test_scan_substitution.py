"""Tests for the tablet scan of resources with compatible-accessory substitution."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from ui.models import (
    Attivita,
    MandrinoItem,
    MascheraItem,
    MppSyncQueue,
    ProblemaAttivita,
    RisorsaAttivita,
    TestaItem,
)
from ui.services.bridge_write import BridgeWriteError
from ui.services.importer import ImportStatus
from ui.services.problem_detection import find_resource_overlaps
from ui.services.scan_substitution import scan_resource
from ui.tests.helpers import create_writer

PLANT = "Valbrembo"


def _ready_status():
    return ImportStatus.READY


def _pairs(att) -> set[tuple[str, str]]:
    return set(
        RisorsaAttivita.objects.filter(attivita_id=att.pk).values_list(
            "tipo", "risorsa_id"
        )
    )


class ScanBase(TestCase):
    def setUp(self) -> None:
        TestaItem.objects.create(uid="T1", model="HT200", plant=PLANT)
        TestaItem.objects.create(uid="T2", model="HT200", plant=PLANT)
        MandrinoItem.objects.create(uid="M102", diameter="22", plant=PLANT)
        MandrinoItem.objects.create(uid="M117", diameter="23,5", plant=PLANT)
        MandrinoItem.objects.create(uid="M120", diameter="24", plant=PLANT)
        MandrinoItem.objects.create(uid="M130", diameter="40", plant=PLANT)
        MandrinoItem.objects.create(uid="M_BAD", diameter="n/d", plant=PLANT)
        MascheraItem.objects.create(uid="MA1", pitch="25.4", plant=PLANT)
        MascheraItem.objects.create(uid="MA2", pitch="25,4", plant=PLANT)
        self.att = Attivita.objects.create(
            commessa="C100",
            item=1,
            activity="Saldatura",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            stabilimento=PLANT,
            modello_testa="HT200",
            bore_min=20,
            bore_max=25,
            bore_mid=22.5,
            tube_pitch=25.4,
        )
        RisorsaAttivita.objects.create(attivita=self.att, tipo="testa", risorsa_id="T1")
        RisorsaAttivita.objects.create(
            attivita=self.att, tipo="mandrino", risorsa_id="M102"
        )
        RisorsaAttivita.objects.create(
            attivita=self.att, tipo="maschera", risorsa_id="MA1"
        )

    def _other(self, uid: str, **overrides) -> Attivita:
        fields = {
            "commessa": "C200",
            "item": 1,
            "activity": "Saldatura",
            "data_inizio": date(2026, 6, 2),
            "data_fine": date(2026, 6, 4),
            "stabilimento": PLANT,
            "modello_testa": "HT200",
            "bore_min": 20,
            "bore_max": 25,
            "bore_mid": 22.5,
        }
        fields.update(overrides)
        other = Attivita.objects.create(**fields)
        RisorsaAttivita.objects.create(attivita=other, tipo="testa", risorsa_id="T2")
        RisorsaAttivita.objects.create(attivita=other, tipo="mandrino", risorsa_id=uid)
        return other


class ExactMatchTest(ScanBase):
    def test_testa_matching_id_is_accepted(self) -> None:
        result = scan_resource(self.att.pk, tipo="testa", codice="T1")
        self.assertEqual(result["esito"], "corrispondente")
        self.assertFalse(result["richiede_conferma"])

    def test_testa_different_id_is_refused_even_if_same_model(self) -> None:
        with self.assertRaises(BridgeWriteError) as ctx:
            scan_resource(self.att.pk, tipo="testa", codice="T2")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.motivo, "non_corrispondente")
        self.assertEqual(
            _pairs(self.att),
            {("testa", "T1"), ("mandrino", "M102"), ("maschera", "MA1")},
        )

    def test_unknown_code_is_404(self) -> None:
        with self.assertRaises(BridgeWriteError) as ctx:
            scan_resource(self.att.pk, tipo="mandrino", codice="NOPE")
        self.assertEqual(ctx.exception.status_code, 404)


class CompatibilityTest(ScanBase):
    def test_expected_accessory_matches_without_checks(self) -> None:
        result = scan_resource(self.att.pk, tipo="mandrino", codice="M102")
        self.assertEqual(result["esito"], "corrispondente")

    def test_out_of_range_is_refused_with_measures(self) -> None:
        with self.assertRaises(BridgeWriteError) as ctx:
            scan_resource(self.att.pk, tipo="mandrino", codice="M130")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.motivo, "non_compatibile")
        self.assertIn("fuori tolleranza", ctx.exception.message)
        self.assertIn("40", ctx.exception.message)
        self.assertIn("20–25", ctx.exception.message)

    def test_unreadable_measure_is_refused_not_treated_as_zero(self) -> None:
        with self.assertRaises(BridgeWriteError) as ctx:
            scan_resource(self.att.pk, tipo="mandrino", codice="M_BAD")
        self.assertEqual(ctx.exception.motivo, "misura")

    def test_missing_requirement_on_activity_refuses_a_different_piece(self) -> None:
        Attivita.objects.filter(pk=self.att.pk).update(bore_min=None, bore_max=None)
        with self.assertRaises(BridgeWriteError) as ctx:
            scan_resource(self.att.pk, tipo="mandrino", codice="M117")
        self.assertEqual(ctx.exception.motivo, "requisiti")

    def test_mask_pitch_with_comma_is_compatible(self) -> None:
        result = scan_resource(self.att.pk, tipo="maschera", codice="MA2")
        self.assertEqual(result["caso"], 1)
        self.assertTrue(result["richiede_conferma"])


class Case1FreeTest(ScanBase):
    def test_without_confirmation_nothing_is_written(self) -> None:
        result = scan_resource(self.att.pk, tipo="mandrino", codice="M117")
        self.assertEqual(result["esito"], "sostituzione")
        self.assertEqual(result["caso"], 1)
        self.assertTrue(result["richiede_conferma"])
        self.assertFalse(result["applicata"])
        self.assertEqual(result["atteso"], "M102")
        self.assertEqual(
            result["messaggio"], "Stai usando M117 al posto di M102, va bene?"
        )
        self.assertIn(("mandrino", "M102"), _pairs(self.att))
        self.assertFalse(MppSyncQueue.objects.exists())

    def test_confirmation_updates_planning_and_keeps_other_categories(self) -> None:
        result = scan_resource(
            self.att.pk, tipo="mandrino", codice="M117", conferma=True
        )
        self.assertTrue(result["applicata"])
        self.assertEqual(result["riassegnazioni"], [])
        self.assertEqual(
            _pairs(self.att),
            {("testa", "T1"), ("mandrino", "M117"), ("maschera", "MA1")},
        )
        self.assertEqual(result["resources_by_tipo"]["mandrino"], ["M117"])
        payload = json.loads(
            MppSyncQueue.objects.get(operation="update_task").payload_json
        )
        self.assertEqual(sorted(payload["resources"]), ["M117", "MA1", "T1"])
        note = ProblemaAttivita.objects.get(attivita=self.att, tipo="sostituzione")
        self.assertTrue(note.risolto)
        self.assertIn("M117", note.descrizione)
        self.assertIn("M102", note.descrizione)
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.att, risolto=False, risorsa_tipo="mandrino"
            ).exists()
        )

    def test_ambiguous_expected_requires_atteso(self) -> None:
        RisorsaAttivita.objects.create(
            attivita=self.att, tipo="mandrino", risorsa_id="M120"
        )
        with self.assertRaises(BridgeWriteError) as ctx:
            scan_resource(self.att.pk, tipo="mandrino", codice="M117")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.motivo, "atteso")

        result = scan_resource(
            self.att.pk, tipo="mandrino", codice="M117", atteso="M120", conferma=True
        )
        self.assertTrue(result["applicata"])
        self.assertEqual(
            {u for t, u in _pairs(self.att) if t == "mandrino"}, {"M102", "M117"}
        )

    def test_atteso_not_assigned_is_400(self) -> None:
        with self.assertRaises(BridgeWriteError) as ctx:
            scan_resource(self.att.pk, tipo="mandrino", codice="M117", atteso="M120")
        self.assertEqual(ctx.exception.motivo, "atteso")


class Case2InUseTest(ScanBase):
    def test_piece_held_by_activity_in_progress_is_refused(self) -> None:
        other = self._other("M117", actual_start=date(2026, 6, 2))
        with self.assertRaises(BridgeWriteError) as ctx:
            scan_resource(self.att.pk, tipo="mandrino", codice="M117", conferma=True)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.motivo, "occupata")
        self.assertEqual(ctx.exception.conflicts[0]["attivita_id"], other.pk)
        self.assertIn(("mandrino", "M102"), _pairs(self.att))
        self.assertIn(("mandrino", "M117"), _pairs(other))
        self.assertFalse(MppSyncQueue.objects.exists())


class Case3PlannedOverlapTest(ScanBase):
    def test_confirmation_message_names_the_robbed_activity(self) -> None:
        other = self._other("M117")
        result = scan_resource(self.att.pk, tipo="mandrino", codice="M117")
        self.assertEqual(result["caso"], 3)
        self.assertTrue(result["richiede_conferma"])
        self.assertTrue(
            result["messaggio"].startswith("Stai usando M117 al posto di M102")
        )
        self.assertEqual(result["attivita_sottratte"][0]["attivita_id"], other.pk)
        self.assertIn(("mandrino", "M117"), _pairs(other))

    def test_robbed_activity_gets_the_freed_piece_as_spare(self) -> None:
        other = self._other("M117")
        result = scan_resource(
            self.att.pk, tipo="mandrino", codice="M117", conferma=True
        )
        self.assertTrue(result["applicata"])
        self.assertEqual(len(result["riassegnazioni"]), 1)
        swap = result["riassegnazioni"][0]
        self.assertEqual(swap["esito"], "sostituita")
        self.assertEqual(
            (swap["attivita_id"], swap["da"], swap["a"]), (other.pk, "M117", "M102")
        )

        self.assertEqual(
            _pairs(self.att),
            {("testa", "T1"), ("mandrino", "M117"), ("maschera", "MA1")},
        )
        self.assertEqual(_pairs(other), {("testa", "T2"), ("mandrino", "M102")})
        self.assertEqual(find_resource_overlaps(), {})
        note = ProblemaAttivita.objects.get(attivita=other, tipo="sostituzione")
        self.assertTrue(note.risolto)
        self.assertEqual(note.riferimento_attivita, self.att.activity_id)
        self.assertIn("M102", note.descrizione)
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                risolto=False, risorsa_tipo="mandrino"
            ).exists()
        )

    def test_robbed_activity_is_reequipped_with_another_compatible_spare(self) -> None:
        other = self._other("M117", bore_min=23, bore_max=25)
        result = scan_resource(
            self.att.pk, tipo="mandrino", codice="M117", conferma=True
        )
        swap = result["riassegnazioni"][0]
        self.assertEqual((swap["esito"], swap["a"]), ("sostituita", "M120"))
        self.assertEqual(_pairs(other), {("testa", "T2"), ("mandrino", "M120")})
        self.assertEqual(find_resource_overlaps(), {})

    def test_robbed_activity_without_spare_is_uncovered_and_flagged(self) -> None:
        MandrinoItem.objects.filter(uid="M120").delete()
        other = self._other("M117", bore_min=23, bore_max=25)
        result = scan_resource(
            self.att.pk, tipo="mandrino", codice="M117", conferma=True
        )
        self.assertTrue(result["applicata"])
        swap = result["riassegnazioni"][0]
        self.assertEqual(swap["esito"], "scoperta")
        self.assertIsNone(swap["a"])

        self.assertIn(("mandrino", "M117"), _pairs(self.att))
        self.assertEqual(_pairs(other), {("testa", "T2")})
        self.assertEqual(find_resource_overlaps(), {})
        acquisto = ProblemaAttivita.objects.get(
            attivita=other, tipo="acquisto", risorsa_tipo="mandrino", risolto=False
        )
        self.assertIn("0/1", acquisto.descrizione)
        note = ProblemaAttivita.objects.get(attivita=other, tipo="sostituzione")
        self.assertIn("Nessuna alternativa", note.descrizione)
        self.assertIn(self.att.activity_id, note.descrizione)
        self.assertIn(str(other.pk), result["activity_updates"])
        self.assertGreaterEqual(
            result["activity_updates"][str(other.pk)]["warning_count"], 1
        )

    def test_scanned_piece_is_not_swapped_away_by_auto_resolve(self) -> None:
        MandrinoItem.objects.filter(uid="M120").delete()
        self._other("M117", bore_min=23, bore_max=25)
        scan_resource(self.att.pk, tipo="mandrino", codice="M117", conferma=True)
        from ui.services.overlap_reassign import auto_resolve_resource_overlaps

        auto_resolve_resource_overlaps()
        self.assertIn(("mandrino", "M117"), _pairs(self.att))


@patch("ui.views_bridge.get_status", side_effect=_ready_status)
class BridgeScanEndpointTest(ScanBase):
    def setUp(self) -> None:
        super().setUp()
        create_writer("tablet", password="pass")
        self.client = Client()
        self.client.login(username="tablet", password="pass")

    def _post(self, body: dict):
        return self.client.post(
            reverse("api_bridge_attivita_scan", kwargs={"pk": self.att.pk}),
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_scan_requires_confirmation_then_applies(self, _mock) -> None:
        resp = self._post({"tipo": "mandrino", "codice": "M117"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertTrue(data["richiede_conferma"])
        self.assertIn(("mandrino", "M102"), _pairs(self.att))

        resp = self._post({"tipo": "mandrino", "codice": "M117", "conferma": True})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["data"]["applicata"])
        self.assertIn(("mandrino", "M117"), _pairs(self.att))

    def test_refusal_carries_motivo(self, _mock) -> None:
        resp = self._post({"tipo": "mandrino", "codice": "M130"})
        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["motivo"], "non_compatibile")

    def test_in_use_refusal_carries_conflicts(self, _mock) -> None:
        self._other("M117", actual_start=date(2026, 6, 2))
        resp = self._post({"tipo": "mandrino", "codice": "M117", "conferma": True})
        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertEqual(body["motivo"], "occupata")
        self.assertEqual(len(body["conflicts"]), 1)

    def test_invalid_tipo_is_400(self, _mock) -> None:
        resp = self._post({"tipo": "pinza", "codice": "X"})
        self.assertEqual(resp.status_code, 400)
