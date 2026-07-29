"""Tests for the add-activity wizard API flow."""

import json
from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase

from ui.models import (
    Attivita,
    ConfigurazioneTesta,
    Job,
    MppSyncQueue,
    TestaItem,
    TipoAttivita,
)
from ui.services.activity_inventory import find_modello_testa_in_db, get_testa_models


class GetTestaModelsTests(TestCase):
    """Tests for :func:`get_testa_models`."""

    def test_returns_sorted_unique_inventory_models(self) -> None:
        TestaItem.objects.create(uid="t1", model="HT200", plant="Valbrembo")
        TestaItem.objects.create(uid="t2", model="IRIS", plant="Valbrembo")
        TestaItem.objects.create(uid="t3", model="HT200", plant="Valbrembo")
        TestaItem.objects.create(uid="t4", model="", plant="Valbrembo")
        self.assertEqual(get_testa_models(), ["HT200", "IRIS"])


class FindModelloTestaTests(TestCase):
    """Tests for :func:`find_modello_testa_in_db`."""

    def setUp(self) -> None:
        ConfigurazioneTesta.objects.create(modello_testa="HT200")
        ConfigurazioneTesta.objects.create(modello_testa="IRIS-Pro")

    def test_exact_case_insensitive_match(self):
        """Lowercase input matches a DB record via iexact and returns canonical form."""
        self.assertEqual(find_modello_testa_in_db("ht200"), "HT200")

    def test_exact_match_preserves_canonical_case(self):
        """Exact match (same case) returns the stored canonical string."""
        self.assertEqual(find_modello_testa_in_db("HT200"), "HT200")

    def test_partial_match_fallback(self):
        """Partial match is used when no exact record exists."""
        self.assertEqual(find_modello_testa_in_db("IRIS"), "IRIS-Pro")

    def test_no_match_returns_none(self):
        """Returns None when no exact or partial match is found."""
        self.assertIsNone(find_modello_testa_in_db("UNKNOWN"))


class AddActivityFlowTests(TestCase):
    """Cover job metadata and predecessor handling in the wizard endpoints."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(username="planner", password="p")
        self.client.force_login(self.user)
        TipoAttivita.objects.create(nome="Qualifica")

    def _post_activity(self, payload: dict):
        return self.client.post(
            "/api/attivita/salva/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_jobs_check_returns_same_commessa_predecessors(self) -> None:
        """Predecessor candidates include every activity of the same commessa."""
        Job.objects.create(commessa="C001")
        prev = Attivita.objects.create(
            commessa="C001",
            item=2,
            activity="Preparazione",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 3),
        )
        other = Attivita.objects.create(
            commessa="C001",
            item=1,
            activity="Altro item",
            data_inizio=date(2026, 6, 4),
            data_fine=date(2026, 6, 5),
        )

        resp = self.client.get("/api/jobs/check/?commessa=C001&item=2")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["exists"])
        pred_ids = {p["activity_id"] for p in payload["predecessors"]}
        self.assertEqual(pred_ids, {prev.activity_id, other.activity_id})
        self.assertEqual(
            {p["item"] for p in payload["predecessors"]},
            {"1", "2"},
        )

    def test_missing_attivita_is_rejected(self) -> None:
        Job.objects.create(commessa="C010")
        resp = self._post_activity(
            {
                "commessa": "C010",
                "item": 1,
                "attivita": "",
                "data_inizio": "2026-06-10",
                "data_fine": "2026-06-12",
                "risorse": {},
            }
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("obbligatorio", resp.json()["error"].lower())

    def test_invalid_attivita_is_rejected(self) -> None:
        Job.objects.create(commessa="C011")
        resp = self._post_activity(
            {
                "commessa": "C011",
                "item": 1,
                "attivita": "Sconosciuta",
                "data_inizio": "2026-06-10",
                "data_fine": "2026-06-12",
                "risorse": {},
            }
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("non valido", resp.json()["error"].lower())

    def test_new_commessa_requires_delivery_date(self) -> None:
        """Creating an activity for a new commessa without delivery_date is rejected."""
        resp = self._post_activity(
            {
                "commessa": "NEW001",
                "item": 1,
                "attivita": "Qualifica",
                "data_inizio": "2026-06-10",
                "data_fine": "2026-06-12",
                "risorse": {},
            }
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("nuova commessa", resp.json()["error"])
        self.assertFalse(Job.objects.filter(commessa="NEW001").exists())

    def test_new_commessa_created_with_only_delivery_date(self) -> None:
        """Creating an activity for a new commessa succeeds with only delivery_date."""
        resp = self._post_activity(
            {
                "commessa": "NEW003",
                "item": 1,
                "attivita": "Qualifica",
                "data_inizio": "2026-06-10",
                "data_fine": "2026-06-12",
                "risorse": {},
                "new_job": {
                    "commessa": "NEW003",
                    "delivery_date": "2026-07-01",
                },
            }
        )

        self.assertEqual(resp.status_code, 200)
        job = Job.objects.get(commessa="NEW003")
        self.assertEqual(job.delivery_date, "2026-07-01")
        self.assertIsNone(job.penalty)
        self.assertIsNone(job.total_cost)

    def test_new_commessa_accepts_italian_delivery_date(self) -> None:
        """Italian DD/MM/YYYY delivery dates are normalized to ISO."""
        resp = self._post_activity(
            {
                "commessa": "NEW004",
                "item": 1,
                "attivita": "Qualifica",
                "data_inizio": "2026-06-10",
                "data_fine": "2026-06-12",
                "risorse": {},
                "new_job": {
                    "commessa": "NEW004",
                    "delivery_date": "15/07/2026",
                },
            }
        )

        self.assertEqual(resp.status_code, 200)
        job = Job.objects.get(commessa="NEW004")
        self.assertEqual(job.delivery_date, "2026-07-15")

    def test_new_commessa_metadata_is_created_when_all_fields_are_present(self) -> None:
        """When cost and penalty are supplied they are persisted on the Job."""
        resp = self._post_activity(
            {
                "commessa": "NEW002",
                "item": 1,
                "attivita": "Qualifica",
                "data_inizio": "2026-06-10",
                "data_fine": "2026-06-12",
                "risorse": {},
                "new_job": {
                    "commessa": "NEW002",
                    "delivery_date": "2026-07-01",
                    "penalty": "3",
                    "total_cost": "50000",
                },
            }
        )

        self.assertEqual(resp.status_code, 200)
        job = Job.objects.get(commessa="NEW002")
        self.assertEqual(job.delivery_date, "2026-07-01")
        self.assertEqual(job.penalty, 3)
        self.assertEqual(job.total_cost, 50000)

    def test_predecessors_are_saved_and_queued(self) -> None:
        Job.objects.create(commessa="C002")
        prev = Attivita.objects.create(
            commessa="C002",
            item=1,
            activity="Preparazione",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 3),
        )

        resp = self._post_activity(
            {
                "commessa": "C002",
                "item": 1,
                "attivita": "Qualifica",
                "data_inizio": "2026-06-10",
                "data_fine": "2026-06-12",
                "risorse": {},
                "predecessors": [prev.activity_id],
            }
        )

        self.assertEqual(resp.status_code, 200)
        att = Attivita.objects.get(activity="Qualifica")
        self.assertEqual(att.predecessors, prev.activity_id)
        entry = MppSyncQueue.objects.get(operation="add_task")
        payload = json.loads(entry.payload_json)
        self.assertEqual(payload["predecessors"], prev.activity_id)

    def test_predecessor_from_other_item_same_commessa_is_accepted(self) -> None:
        Job.objects.create(commessa="C003")
        prev = Attivita.objects.create(
            commessa="C003",
            item="A1",
            activity="Preparazione",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 3),
        )

        resp = self._post_activity(
            {
                "commessa": "C003",
                "item": "B2",
                "attivita": "Qualifica",
                "data_inizio": "2026-06-10",
                "data_fine": "2026-06-12",
                "risorse": {},
                "predecessors": [prev.activity_id],
            }
        )

        self.assertEqual(resp.status_code, 200)
        att = Attivita.objects.get(commessa="C003", item="B2", activity="Qualifica")
        self.assertEqual(att.predecessors, prev.activity_id)
