"""Rules on running activities: penalty risk on the plan, no swap once started,
planned dates in order."""

from __future__ import annotations

import json
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils.timezone import localdate

from ui.models import Attivita, Job, ProblemaAttivita, RisorsaAttivita, TestaItem
from ui.services.overlap_reassign import (
    STARTED_NO_SWAP_SUMMARY,
    OverlapReassignError,
    apply_overlap_reassignment,
    auto_resolve_resource_overlaps,
)
from ui.services.planning_read import aggregate_job_status, projected_end


class PenaltyRiskLooksAtThePlanTest(TestCase):
    def test_planned_finish_past_the_deadline_is_a_risk_before_starting(self) -> None:
        today = localdate()
        tasks = [
            {
                "state_key": "pianificata",
                "start": (today + timedelta(days=10)).isoformat(),
                "finish": (today + timedelta(days=20)).isoformat(),
            }
        ]
        deadline = (today + timedelta(days=15)).isoformat()
        self.assertEqual(aggregate_job_status(tasks, deadline), "rischio_penale")
        self.assertEqual(
            aggregate_job_status(tasks, (today + timedelta(days=20)).isoformat()),
            "pianificata",
        )

    def test_projected_end_prefers_the_server_estimate(self) -> None:
        task = {
            "start": "2026-06-01",
            "finish": "2026-06-10",
            "actual_start": "2026-06-05",
            "effective_finish": "2026-06-16",
        }
        self.assertEqual(projected_end(task), date(2026, 6, 16))
        # Without the field the same working-day rule is applied.
        del task["effective_finish"]
        self.assertEqual(projected_end(task), date(2026, 6, 16))
        self.assertEqual(
            projected_end({"start": "2026-06-01", "finish": "2026-06-10"}),
            date(2026, 6, 10),
        )
        self.assertIsNone(projected_end({}))


class NoSwapOnStartedActivityTest(TestCase):
    """A running activity keeps its physical resources, overlap or not."""

    def setUp(self) -> None:
        today = localdate()
        TestaItem.objects.create(uid="T1", model="TP60", plant="Valbrembo")
        TestaItem.objects.create(uid="T2", model="TP60", plant="Valbrembo")
        Job.objects.create(commessa="C1", job_id="C1")
        self.first = Attivita.objects.create(
            commessa="C1",
            item="1",
            activity="A",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=today - timedelta(days=10),
            data_fine=today - timedelta(days=4),
            actual_start=today - timedelta(days=6),
        )
        # Both started: the one planned later is the "blocked" side.
        self.second = Attivita.objects.create(
            commessa="C1",
            item="2",
            activity="B",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=today - timedelta(days=3),
            data_fine=today + timedelta(days=3),
            actual_start=today - timedelta(days=2),
        )
        for att in (self.first, self.second):
            RisorsaAttivita.objects.create(attivita=att, tipo="testa", risorsa_id="T1")

    def test_auto_resolve_leaves_a_started_activity_alone(self) -> None:
        result = auto_resolve_resource_overlaps()
        self.assertEqual(result["swapped_blocked_ids"], [])
        self.assertEqual(
            list(self.second.risorse.values_list("risorsa_id", flat=True)), ["T1"]
        )
        note = ProblemaAttivita.objects.get(
            attivita=self.second,
            tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
            risolto=False,
        )
        self.assertEqual(note.risorsa_uid, "T1")

    def test_manual_apply_is_refused(self) -> None:
        with self.assertRaises(OverlapReassignError) as ctx:
            apply_overlap_reassignment(self.second.pk)
        self.assertEqual(str(ctx.exception), STARTED_NO_SWAP_SUMMARY)
        self.assertEqual(ctx.exception.status_code, 409)


class PlannedDatesInOrderTest(TestCase):
    def setUp(self) -> None:
        user = User.objects.create_user(username="planner", password="p")
        from ui.permissions import get_or_create_profile

        profile = get_or_create_profile(user)
        profile.ruolo = "scrittura"
        profile.save()
        self.client = Client()
        self.client.force_login(user)
        self.att = Attivita.objects.create(
            commessa="C1",
            item="1",
            activity="A",
            data_inizio=date(2026, 11, 2),
            data_fine=date(2026, 11, 6),
        )

    def test_update_refuses_a_finish_before_the_start(self) -> None:
        resp = self.client.put(
            "/api/tasks/update/",
            data=json.dumps(
                {"orm_id": self.att.pk, "start": "2026-11-10", "finish": "2026-11-06"}
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.att.refresh_from_db()
        self.assertEqual(self.att.data_inizio, date(2026, 11, 2))
