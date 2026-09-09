"""Tests for applying accepted optimization proposals."""

from __future__ import annotations

import json
from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from ui.models import (
    Attivita,
    GeneratoreItem,
    Job,
    MandrinoItem,
    MppSyncQueue,
    RisorsaAttivita,
    TastatoreItem,
)
from ui.services.optimization_apply import (
    OptimizationApplyError,
    apply_optimization_result,
    solver_resource_id_to_assignment,
)
from ui.views_ottimizzazione import SESSION_EXPLANATION_KEY, SESSION_RESULT_KEY
from ui.tests.helpers import create_writer


def _seed_inventory() -> None:
    GeneratoreItem.objects.get_or_create(
        uid="G501", defaults={"model": "g1", "plant": "Valbrembo"}
    )
    MandrinoItem.objects.get_or_create(
        uid="M12", defaults={"diameter": "15", "plant": "Valbrembo"}
    )
    TastatoreItem.objects.get_or_create(
        uid="T9", defaults={"diameter": "15", "plant": "Valbrembo"}
    )


def _make_activity(
    commessa: str,
    item: int,
    activity: str,
    *,
    di: str = "2026-06-01",
    df: str = "2026-06-05",
) -> Attivita:
    att = Attivita(
        commessa=commessa,
        item=item,
        activity=activity,
        data_inizio=date.fromisoformat(di),
        data_fine=date.fromisoformat(df),
        duration_min=480,
        stabilimento="Valbrembo",
    )
    att.save()
    return att


class SolverResourceMappingTest(TestCase):
    def test_full_solver_id(self) -> None:
        self.assertEqual(
            solver_resource_id_to_assignment("generatore:G501"),
            ("generatore", "G501"),
        )

    def test_stripped_uid_lookup(self) -> None:
        _seed_inventory()
        self.assertEqual(
            solver_resource_id_to_assignment("G501"),
            ("generatore", "G501"),
        )


class ApplyOptimizationResultTest(TestCase):
    def setUp(self) -> None:
        _seed_inventory()
        Job.objects.create(commessa="T010", delivery_date="2026-12-31")
        self.att = _make_activity("T010", 1, "prep")
        RisorsaAttivita.objects.create(
            attivita=self.att, tipo="generatore", risorsa_id="G501"
        )

    def test_updates_dates_and_resources(self) -> None:
        payload = {
            "job_ids": [self.att.job_id],
            "tasks_after": [
                {
                    "orm_id": self.att.pk,
                    "activity_id": self.att.activity_id,
                    "start": "2026-07-01",
                    "finish": "2026-07-05",
                    "assigned_resource_ids": [
                        "generatore:G501",
                        "mandrino:M12",
                        "tastatore:T9",
                    ],
                }
            ],
        }
        updated = apply_optimization_result(payload)
        self.assertEqual(updated, 1)

        self.att.refresh_from_db()
        self.assertEqual(self.att.data_inizio.isoformat(), "2026-07-01")
        self.assertEqual(self.att.data_fine.isoformat(), "2026-07-05")

        pairs = list(
            RisorsaAttivita.objects.filter(attivita=self.att)
            .order_by("tipo")
            .values_list("tipo", "risorsa_id")
        )
        self.assertEqual(
            pairs,
            [
                ("generatore", "G501"),
                ("mandrino", "M12"),
                ("tastatore", "T9"),
            ],
        )
        self.assertEqual(
            MppSyncQueue.objects.filter(operation="update_task").count(), 1
        )

    def _queued_payload(self) -> dict:
        entry = MppSyncQueue.objects.get(operation="update_task")
        return json.loads(entry.payload_json)

    def test_mpp_payload_carries_the_reassigned_resources(self) -> None:
        """Without the key the write-back leaves the .mpp resources untouched,
        so accepting a proposal would desync the file from the database."""
        apply_optimization_result(
            {
                "job_ids": [self.att.job_id],
                "tasks_after": [
                    {
                        "orm_id": self.att.pk,
                        "activity_id": self.att.activity_id,
                        "start": "2026-07-01",
                        "finish": "2026-07-05",
                        "assigned_resource_ids": ["mandrino:M12", "tastatore:T9"],
                    }
                ],
            }
        )

        payload = self._queued_payload()
        self.assertIn("resources", payload)
        self.assertEqual(sorted(payload["resources"]), ["M12", "T9"])

    def test_mpp_payload_clears_resources_when_proposal_assigns_none(self) -> None:
        """An empty list is still sent: the rows were deleted in the database
        and the .mpp has to follow, which an absent key would not do."""
        apply_optimization_result(
            {
                "job_ids": [self.att.job_id],
                "tasks_after": [
                    {
                        "orm_id": self.att.pk,
                        "activity_id": self.att.activity_id,
                        "start": "2026-07-01",
                        "finish": "2026-07-05",
                    }
                ],
            }
        )

        self.assertFalse(RisorsaAttivita.objects.filter(attivita=self.att).exists())
        payload = self._queued_payload()
        self.assertIn("resources", payload)
        self.assertEqual(payload["resources"], [])

    def test_empty_tasks_raises(self) -> None:
        with self.assertRaises(OptimizationApplyError):
            apply_optimization_result({"tasks_after": []})

    def test_no_false_conflict_with_stale_dates_of_same_proposal(self) -> None:
        """New dates overlapping another activity's OLD booking must not conflict.

        Regression: the proposal moves both activities, so each must be
        validated against the applied state, not the pre-apply dates.
        """
        # Old plan: att (prep) Jun 1-5 with G501; att2 (work) Jun 8-12 with G501.
        att2 = _make_activity("T010", 2, "work", di="2026-06-08", df="2026-06-12")
        RisorsaAttivita.objects.create(
            attivita=att2, tipo="generatore", risorsa_id="G501"
        )
        # New plan shifts att onto att2's old range and att2 later.
        payload = {
            "job_ids": [self.att.job_id],
            "tasks_after": [
                {
                    "orm_id": self.att.pk,
                    "activity_id": self.att.activity_id,
                    "start": "2026-06-08",
                    "finish": "2026-06-12",
                    "assigned_resource_ids": ["generatore:G501"],
                },
                {
                    "orm_id": att2.pk,
                    "activity_id": att2.activity_id,
                    "start": "2026-06-15",
                    "finish": "2026-06-19",
                    "assigned_resource_ids": ["generatore:G501"],
                },
            ],
        }
        updated = apply_optimization_result(payload)
        self.assertEqual(updated, 2)
        att2.refresh_from_db()
        self.assertEqual(att2.data_inizio.isoformat(), "2026-06-15")

    def test_skips_in_progress_activity(self) -> None:
        self.att.actual_start = date(2026, 6, 1)
        self.att.save()
        payload = {
            "job_ids": [self.att.job_id],
            "tasks_after": [
                {
                    "orm_id": self.att.pk,
                    "activity_id": self.att.activity_id,
                    "start": "2026-09-01",
                    "finish": "2026-09-05",
                    "assigned_resource_ids": ["generatore:G501"],
                }
            ],
        }
        updated = apply_optimization_result(payload)
        self.assertEqual(updated, 0)
        self.att.refresh_from_db()
        self.assertEqual(self.att.data_inizio.isoformat(), "2026-06-01")

    def test_real_conflict_detected_and_rolled_back(self) -> None:
        """Same resource on two overlapping proposed ranges must still fail."""
        from ui.services.resource_conflicts import ResourceConflictError

        att2 = _make_activity("T010", 2, "work", di="2026-06-08", df="2026-06-12")
        payload = {
            "job_ids": [self.att.job_id],
            "tasks_after": [
                {
                    "orm_id": self.att.pk,
                    "activity_id": self.att.activity_id,
                    "start": "2026-07-01",
                    "finish": "2026-07-05",
                    "assigned_resource_ids": ["generatore:G501"],
                },
                {
                    "orm_id": att2.pk,
                    "activity_id": att2.activity_id,
                    "start": "2026-07-03",
                    "finish": "2026-07-08",
                    "assigned_resource_ids": ["generatore:G501"],
                },
            ],
        }
        with self.assertRaises(ResourceConflictError):
            apply_optimization_result(payload)
        # The whole transaction must roll back: original dates untouched.
        self.att.refresh_from_db()
        self.assertEqual(self.att.data_inizio.isoformat(), "2026-06-01")
        att2.refresh_from_db()
        self.assertEqual(att2.data_inizio.isoformat(), "2026-06-08")
        self.assertEqual(MppSyncQueue.objects.count(), 0)


class OptimizationAcceptApiTest(TestCase):
    def setUp(self) -> None:
        _seed_inventory()
        self.user = create_writer("acceptuser", password="pass")
        self.client = Client()
        self.client.force_login(self.user)
        Job.objects.create(commessa="T011", delivery_date="2026-12-31")
        self.att = _make_activity("T011", 1, "work")
        RisorsaAttivita.objects.create(
            attivita=self.att, tipo="generatore", risorsa_id="G501"
        )
        self.payload = {
            "job_ids": [self.att.job_id],
            "tasks_after": [
                {
                    "orm_id": self.att.pk,
                    "activity_id": self.att.activity_id,
                    "start": "2026-08-01",
                    "finish": "2026-08-06",
                    "assigned_resource_ids": ["generatore:G501"],
                }
            ],
        }

    def test_requires_session(self) -> None:
        resp = self.client.post(reverse("api_ottimizzazione_accetta"))
        self.assertEqual(resp.status_code, 400)

    def test_applies_and_clears_session(self) -> None:
        session = self.client.session
        session[SESSION_RESULT_KEY] = self.payload
        session[SESSION_EXPLANATION_KEY] = "Spiegazione."
        session.save()

        resp = self.client.post(reverse("api_ottimizzazione_accetta"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["updated"], 1)
        self.assertIn("pianificazione", data["redirect_url"])

        self.att.refresh_from_db()
        self.assertEqual(self.att.data_inizio.isoformat(), "2026-08-01")
        self.assertNotIn(SESSION_RESULT_KEY, self.client.session)
