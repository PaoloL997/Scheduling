"""Tests for overlap reassignment suggestions and accept API."""

from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from ui.models import Attivita, RisorsaAttivita, TestaItem
from ui.services.overlap_reassign import (
    apply_overlap_reassignment,
    build_suggestion,
    enrich_overlaps_with_suggestions,
)
from ui.services.problem_detection import find_resource_overlaps


class OverlapReassignTest(TestCase):
    """Suggest and apply a replacement for the blocked activity."""

    def setUp(self) -> None:
        self.late = Attivita.objects.create(
            commessa="C101",
            item=1,
            activity="Saldatura",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 10),
            data_fine=date(2026, 8, 19),
            actual_start=date(2026, 8, 17),
        )
        self.blocked = Attivita.objects.create(
            commessa="C205",
            item=2,
            activity="Montaggio",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 20),
            data_fine=date(2026, 8, 29),
        )
        for att in (self.late, self.blocked):
            RisorsaAttivita.objects.create(
                attivita=att, tipo="testa", risorsa_id="OV-T1"
            )
        TestaItem.objects.create(uid="OV-T1", model="TP60", plant="Valbrembo")

    def _overlap_item(self) -> dict:
        result = find_resource_overlaps()
        return result[self.blocked.pk][0]

    def test_suggests_free_same_plant_testa(self) -> None:
        TestaItem.objects.create(uid="OV-T2", model="TP60", plant="Valbrembo")
        activities = list(Attivita.objects.prefetch_related("risorse"))
        sug = build_suggestion(self.blocked, self._overlap_item(), activities)
        self.assertEqual(len(sug["replacements"]), 1)
        self.assertEqual(sug["replacements"][0]["from_uid"], "OV-T1")
        self.assertEqual(sug["replacements"][0]["to_uid"], "OV-T2")
        self.assertEqual(sug["replacements"][0]["from_item"]["plant"], "Valbrembo")
        self.assertEqual(sug["replacements"][0]["from_item"]["model"], "TP60")
        self.assertEqual(sug["replacements"][0]["to_item"]["plant"], "Valbrembo")
        self.assertEqual(sug["replacements"][0]["to_item"]["model"], "TP60")
        self.assertEqual(sug["blocked_plant"], "Valbrembo")
        self.assertIn("OV-T2", sug["summary"])
        self.assertIn("TP60", sug["summary"])
        self.assertIn("Valbrembo", sug["summary"])
        self.assertIn("Proposta sostituzione", sug["summary"])
        self.assertFalse(sug["unresolved"])
        self.assertEqual(sug["blocked_orm_id"], self.blocked.pk)

    def test_no_alternative_when_inventory_empty(self) -> None:
        activities = list(Attivita.objects.prefetch_related("risorse"))
        sug = build_suggestion(self.blocked, self._overlap_item(), activities)
        self.assertEqual(sug["replacements"], [])
        self.assertEqual(sug["unresolved"][0]["from_uid"], "OV-T1")

    def test_does_not_suggest_delay_occupied_resource(self) -> None:
        TestaItem.objects.create(uid="OV-T2", model="TP60", plant="Valbrembo")
        other = Attivita.objects.create(
            commessa="OTHER",
            item=1,
            activity="Altro",
            stabilimento="Valbrembo",
            data_inizio=date(2026, 8, 1),
            data_fine=date(2026, 8, 10),
            actual_start=date(2026, 8, 12),
        )
        RisorsaAttivita.objects.create(attivita=other, tipo="testa", risorsa_id="OV-T2")
        activities = list(Attivita.objects.prefetch_related("risorse"))
        sug = build_suggestion(self.blocked, self._overlap_item(), activities)
        self.assertEqual(sug["replacements"], [])

    def test_enrich_attaches_suggestion_on_both_sides(self) -> None:
        TestaItem.objects.create(uid="OV-T2", model="TP60", plant="Valbrembo")
        activities = list(Attivita.objects.prefetch_related("risorse"))
        result = find_resource_overlaps(activities)
        enrich_overlaps_with_suggestions(result, activities)
        self.assertEqual(
            result[self.blocked.pk][0]["suggestion"]["replacements"][0]["to_uid"],
            "OV-T2",
        )
        self.assertEqual(
            result[self.late.pk][0]["suggestion"]["replacements"][0]["to_uid"],
            "OV-T2",
        )

    def test_apply_reassigns_blocked_and_clears_overlap(self) -> None:
        TestaItem.objects.create(uid="OV-T2", model="TP60", plant="Valbrembo")
        result = apply_overlap_reassignment(self.blocked.pk)
        self.assertEqual(result["blocked_orm_id"], self.blocked.pk)
        self.assertEqual(
            list(
                RisorsaAttivita.objects.filter(attivita=self.blocked).values_list(
                    "risorsa_id", flat=True
                )
            ),
            ["OV-T2"],
        )
        self.assertEqual(
            list(
                RisorsaAttivita.objects.filter(attivita=self.late).values_list(
                    "risorsa_id", flat=True
                )
            ),
            ["OV-T1"],
        )
        self.assertEqual(find_resource_overlaps(), {})


class OverlapReassignApiTest(TestCase):
    """POST /api/attivita/<pk>/overlap-reassign/."""

    def setUp(self) -> None:
        self.user = User.objects.create_user("planner", password="pass")
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
        TestaItem.objects.create(uid="OV-T1", model="TP60", plant="Valbrembo")
        TestaItem.objects.create(uid="OV-T2", model="TP60", plant="Valbrembo")

    def test_requires_login(self) -> None:
        url = reverse("api_overlap_reassign", args=[self.blocked.pk])
        resp = self.client.post(url, data="{}", content_type="application/json")
        self.assertEqual(resp.status_code, 302)

    def test_accept_from_blocked_clears_overlap(self) -> None:
        self.client.force_login(self.user)
        url = reverse("api_overlap_reassign", args=[self.blocked.pk])
        resp = self.client.post(url, data="{}", content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["blocked_orm_id"], self.blocked.pk)
        self.assertEqual(body["resources_by_tipo"]["testa"], ["OV-T2"])
        self.assertEqual(find_resource_overlaps(), {})

    def test_accept_from_late_reassigns_blocked(self) -> None:
        self.client.force_login(self.user)
        url = reverse("api_overlap_reassign", args=[self.late.pk])
        resp = self.client.post(
            url,
            data=f'{{"blocked_orm_id": {self.blocked.pk}}}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            RisorsaAttivita.objects.get(attivita=self.blocked).risorsa_id,
            "OV-T2",
        )
