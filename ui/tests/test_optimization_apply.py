"""Tests for applying accepted optimization proposals."""

from __future__ import annotations

from datetime import date

from django.contrib.auth.models import User
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

    def test_empty_tasks_raises(self) -> None:
        with self.assertRaises(OptimizationApplyError):
            apply_optimization_result({"tasks_after": []})


class OptimizationAcceptApiTest(TestCase):
    def setUp(self) -> None:
        _seed_inventory()
        self.user = User.objects.create_user("acceptuser", password="pass")
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
