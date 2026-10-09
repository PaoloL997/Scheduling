"""The planning read layer: pure, cheap, and the only place states are computed."""

from __future__ import annotations

import json
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils.timezone import localdate

from ui.models import (
    Attivita,
    ConfigurazioneTesta,
    Job,
    OperationLog,
    ProblemaAttivita,
    RisorsaAttivita,
)
from ui.services.planning_read import (
    compute_delay_days,
    compute_work_progress,
    progress_percent,
    fetch_gantt,
    fetch_grouped,
    fetch_tasks,
    job_summary,
)

TODAY = date(2026, 9, 10)


def _att(commessa, item, activity, **fields) -> Attivita:
    return Attivita.objects.create(
        commessa=commessa, item=str(item), activity=activity, **fields
    )


class DelayDaysTest(TestCase):
    """``delay_days`` is zero exactly when the activity is not late."""

    def test_late_start(self) -> None:
        self.assertEqual(
            compute_delay_days(
                date(2026, 9, 1), date(2026, 9, 20), date(2026, 9, 4), None, TODAY
            ),
            3,
        )

    def test_waiting_to_start_counts_from_the_day_after_the_planned_start(self) -> None:
        # On the planned start day the operator still has the whole day.
        self.assertEqual(
            compute_delay_days(TODAY, TODAY + timedelta(days=5), None, None, TODAY), 0
        )
        self.assertEqual(
            compute_delay_days(
                TODAY - timedelta(days=5), TODAY + timedelta(days=5), None, None, TODAY
            ),
            5,
        )

    def test_open_past_planned_finish(self) -> None:
        self.assertEqual(
            compute_delay_days(
                date(2026, 8, 20), date(2026, 8, 31), date(2026, 8, 20), None, TODAY
            ),
            10,
        )

    def test_not_late(self) -> None:
        self.assertEqual(
            compute_delay_days(
                date(2026, 10, 1), date(2026, 10, 15), None, None, TODAY
            ),
            0,
        )
        self.assertEqual(
            compute_delay_days(
                date(2026, 8, 20),
                date(2026, 8, 31),
                date(2026, 8, 25),
                date(2026, 9, 5),
                TODAY,
            ),
            0,
        )
        self.assertEqual(compute_delay_days(None, None, None, None, TODAY), 0)


class WorkProgressTest(TestCase):
    """Working days done / planned, the figure behind every progress bar."""

    def test_progress(self) -> None:
        mon, fri = date(2026, 6, 1), date(2026, 6, 5)
        self.assertEqual(compute_work_progress(None, fri, None, None, TODAY), (0, 0))
        self.assertEqual(compute_work_progress(mon, fri, None, None, TODAY), (0, 5))
        self.assertEqual(compute_work_progress(mon, fri, mon, fri, TODAY), (5, 5))
        # Started Monday, today is Wednesday: three working days in.
        self.assertEqual(
            compute_work_progress(mon, fri, mon, None, date(2026, 6, 3)), (3, 5)
        )
        # Still open past the planned span: only actual_finish completes it.
        self.assertEqual(compute_work_progress(mon, fri, mon, None, TODAY), (4, 5))
        one_day = compute_work_progress(mon, mon, mon, None, TODAY)
        self.assertEqual(one_day, (0, 1))
        # Start recorded in the future: nothing done yet.
        self.assertEqual(
            compute_work_progress(mon, fri, date(2026, 6, 4), None, date(2026, 6, 2)),
            (0, 5),
        )
        self.assertEqual(progress_percent(1, 3), 33)
        self.assertEqual(progress_percent(1, 2), 50)
        self.assertEqual(progress_percent(0, 0), 0)
        self.assertEqual(progress_percent(5, 5), 100)
        # Commessa 25078: open activities left, so never shown as 100%.
        self.assertEqual(progress_percent(199, 200), 99)


class ReadLayerIsPureTest(TestCase):
    """Reading the planning data never writes and costs a fixed number of queries."""

    def setUp(self) -> None:
        today = localdate()
        Job.objects.create(commessa="C1", job_id="C1", delivery_date=today)
        ConfigurazioneTesta.objects.create(modello_testa="TP60", min_mandrini=2)
        first = None
        for i in range(1, 7):
            att = _att(
                "C1",
                i,
                f"A{i}",
                modello_testa="TP60",
                data_inizio=today - timedelta(days=20),
                data_fine=today - timedelta(days=10),
            )
            if first is None:
                first = att
            else:
                att.predecessori.set([first])
            RisorsaAttivita.objects.create(attivita=att, tipo="testa", risorsa_id="T1")
        _att("C2", 1, "B1")

    def test_fetch_tasks_costs_a_fixed_number_of_queries(self) -> None:
        # activities + risorse + problemi + predecessori + testa configs
        # (+ their compatible generators): six, whatever the number of
        # activities.
        with self.assertNumQueries(6):
            tasks = fetch_tasks()
        self.assertEqual(len(tasks), 7)

    def test_fetch_grouped_does_not_write(self) -> None:
        problems = ProblemaAttivita.objects.count()
        logs = OperationLog.objects.count()
        before = list(RisorsaAttivita.objects.values_list("attivita_id", "risorsa_id"))

        fetch_grouped()

        self.assertEqual(ProblemaAttivita.objects.count(), problems)
        self.assertEqual(OperationLog.objects.count(), logs)
        self.assertEqual(
            list(RisorsaAttivita.objects.values_list("attivita_id", "risorsa_id")),
            before,
        )

    def test_rows_carry_the_computed_state(self) -> None:
        tasks = {t["activity_id"]: t for t in fetch_tasks()}
        late = tasks["C1-1-A1"]
        self.assertEqual(late["state_key"], "in_ritardo")
        self.assertGreater(late["delay_days"], 0)
        self.assertEqual(
            [p["key"] for p in late["status_pills"]], ["in_ritardo", "scaduta"]
        )
        self.assertEqual(json.loads(late["status_pills_json"]), late["status_pills"])
        self.assertEqual(late["min_requirements"]["mandrini"], 2)
        self.assertEqual(tasks["C1-2-A2"]["predecessor_names"], "A1")
        unplanned = tasks["C2-1-B1"]
        self.assertEqual(unplanned["state_key"], "non_pianificata")
        self.assertEqual(unplanned["delay_days"], 0)

    def test_groups_carry_the_job_status(self) -> None:
        groups = {g["job"]["commessa"]: g["job"] for g in fetch_grouped()}
        self.assertEqual(groups["C1"]["status"], "in_ritardo")
        self.assertEqual(groups["C1"]["status_label"], "IN RITARDO")
        # Six planned, none started: a span to do, nothing done yet.
        self.assertGreater(groups["C1"]["work_total"], 0)
        self.assertEqual(groups["C1"]["work_done"], 0)
        self.assertEqual(groups["C1"]["progress_pct"], 0)
        self.assertFalse(groups["C1"]["completed"])
        # A commessa with activities but no planned dates still reads
        # "pianificata": only an empty commessa is "non_pianificata".
        self.assertEqual(groups["C2"]["status"], "pianificata")

    def test_job_summary_matches_the_group(self) -> None:
        group = next(g["job"] for g in fetch_grouped() if g["job"]["commessa"] == "C1")
        summary = job_summary("C1")
        for key in (
            "status",
            "status_label",
            "completed",
            "n_open_problems",
            "n_activities",
        ):
            self.assertEqual(summary[key], group[key], key)

    def test_gantt_payload_carries_states_and_job_statuses(self) -> None:
        payload = fetch_gantt()
        task = next(t for t in payload["tasks"] if t["activity_id"] == "C1-1-A1")
        self.assertEqual(task["state_key"], "in_ritardo")
        self.assertIn("plant_color", task)
        self.assertEqual(task["moving_resources"], [])
        self.assertEqual(payload["jobs"]["C1"]["status"], "in_ritardo")
        self.assertEqual(payload["deadlines"]["C1"], localdate().isoformat())


class WriteResponsesCarryTheStateTest(TestCase):
    """Clients display the state the write response returns; they never recompute it."""

    def setUp(self) -> None:
        self.client = Client()
        user = User.objects.create_user(username="planner", password="p")
        from ui.permissions import get_or_create_profile

        profile = get_or_create_profile(user)
        profile.ruolo = "scrittura"
        profile.save()
        self.client.force_login(user)
        today = localdate()
        Job.objects.create(
            commessa="C1", job_id="C1", delivery_date=today + timedelta(days=30)
        )
        self.att = _att(
            "C1",
            1,
            "A1",
            data_inizio=today + timedelta(days=5),
            data_fine=today + timedelta(days=10),
        )

    def test_task_update_returns_the_row_and_the_job(self) -> None:
        today = localdate()
        resp = self.client.put(
            "/api/tasks/update/",
            data=json.dumps(
                {
                    "orm_id": self.att.pk,
                    "start": (today - timedelta(days=10)).isoformat(),
                    "finish": (today - timedelta(days=5)).isoformat(),
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["task"]["state_key"], "in_ritardo")
        # Never started, planned start ten days ago: ten days waiting (more
        # than the five past the planned finish).
        self.assertEqual(body["task"]["delay_days"], 10)
        self.assertEqual(body["job"]["commessa"], "C1")
        self.assertEqual(body["job"]["status"], "in_ritardo")

    def test_job_update_returns_the_job(self) -> None:
        resp = self.client.put(
            "/api/jobs/update/",
            data=json.dumps({"commessa": "C1", "delivery_date": "2026-12-31"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["job"]["status"], "pianificata")
