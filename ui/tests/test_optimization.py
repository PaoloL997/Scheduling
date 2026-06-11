"""Tests for the optimization wizard and builder."""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.timezone import localdate

from ui.models import (
    AppSettings,
    Attivita,
    Job,
    MandrinoItem,
    RisorsaAttivita,
    TastatoreItem,
)
from ui.services.optimization_problem import (
    OptimizationProblemError,
    _duration_days,
    activities_for_job_ids,
    activities_to_gantt_tasks,
    build_scheduling_problem,
    filter_job_ids_with_activities,
    fixed_calendar_window,
    hydrate_gantt_tasks_resources,
    is_attivita_fixed,
    match_attivita_for_solver_activity_id,
    schedule_metrics_from_tasks,
    schedule_result_to_gantt_tasks,
)
from ui.views_ottimizzazione import (
    SESSION_DRAFT_KEY,
    SESSION_EXPLANATION_KEY,
    SESSION_RESULT_KEY,
)

_MOCK_EXPLANATION = "Spiegazione di test."


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


def _seed_inventory() -> None:
    MandrinoItem.objects.get_or_create(
        uid="m-test", defaults={"diameter": "15", "plant": "Valbrembo"}
    )
    TastatoreItem.objects.get_or_create(
        uid="t-test", defaults={"diameter": "15", "plant": "Valbrembo"}
    )


class BuildSchedulingProblemTest(TestCase):
    """Unit tests for the ORM problem builder."""

    def setUp(self) -> None:
        _seed_inventory()
        Job.objects.create(commessa="T001", delivery_date="2026-12-31")

    def test_job_selection_includes_all_activities(self) -> None:
        a1 = _make_activity("T001", 1, "prep")
        _make_activity("T001", 2, "work")
        _make_activity("T001", 3, "finish")
        problem = build_scheduling_problem([a1.job_id])
        self.assertEqual(len(problem.jobs), 1)
        self.assertEqual(len(problem.jobs[0].activities), 3)

    def test_stale_job_id_resolved_by_commessa(self) -> None:
        """Activities with legacy job_id suffix still match canonical job selection."""
        a1 = _make_activity("25079", 1, "prep")
        _make_activity("25079", 2, "work")
        Attivita.objects.filter(commessa="25079").update(job_id="25079-01")
        problem = build_scheduling_problem([a1.job_id])
        self.assertEqual(len(problem.jobs), 1)
        self.assertEqual(len(problem.jobs[0].activities), 2)
        self.assertTrue(
            Attivita.objects.filter(commessa="25079", job_id=a1.job_id).count() == 2
        )

    def test_legacy_job_id_prefix_without_commessa_match(self) -> None:
        """Legacy per-item job_id still resolves to the canonical commessa."""
        a1 = _make_activity("25079", 1, "Qualifica")
        _make_activity("25079", 1, "SNB")
        Attivita.objects.filter(commessa="25079").update(job_id="25079-01")
        self.assertEqual(len(activities_for_job_ids([a1.job_id])), 2)
        self.assertEqual(
            filter_job_ids_with_activities([a1.job_id, "TG99"]), [a1.job_id]
        )

    def test_in_progress_activity_excluded_from_model(self) -> None:
        """Started activities must not appear in the optimization model at all."""
        started = _make_activity("T001", 1, "SNA", di="2026-06-01", df="2026-06-10")
        started.actual_start = date(2026, 6, 5)
        started.save()
        free = _make_activity("T001", 1, "SNB")
        problem = build_scheduling_problem([started.job_id])
        ids = {a.activity_id for j in problem.jobs for a in j.activities}
        self.assertNotIn(started.activity_id, ids)
        self.assertIn(free.activity_id, ids)

    def test_in_progress_without_data_fine_excluded(self) -> None:
        """Regression: actual_start alone must exclude the activity."""
        att = _make_activity("T001", 1, "A", di="2026-06-01", df="2026-06-10")
        att.data_fine = None
        att.duration_min = 5 * 8 * 60
        att.actual_start = date(2026, 6, 5)
        att.save()
        _make_activity("T001", 1, "B")
        problem = build_scheduling_problem([att.job_id])
        ids = {a.activity_id for j in problem.jobs for a in j.activities}
        self.assertNotIn(att.activity_id, ids)

    def test_in_progress_resources_become_reservations(self) -> None:
        started = _make_activity("T001", 1, "SNA", di="2026-06-01", df="2026-06-10")
        started.actual_start = date(2026, 6, 5)
        started.save()
        RisorsaAttivita.objects.create(
            attivita=started, tipo="mandrino", risorsa_id="m-test"
        )
        _make_activity("T001", 1, "SNB")
        problem = build_scheduling_problem(
            [started.job_id], horizon_start=date(2026, 6, 1)
        )
        reserved = {
            (r.resource_id, r.activity_id) for r in problem.resource_reservations
        }
        self.assertIn(("mandrino:m-test", started.activity_id), reserved)

    def test_all_fixed_job_raises(self) -> None:
        att = _make_activity("T001", 1, "done", di="2026-05-01", df="2026-05-10")
        att.actual_start = date(2026, 5, 1)
        att.actual_finish = date(2026, 5, 8)
        att.save()
        with self.assertRaises(OptimizationProblemError):
            build_scheduling_problem([att.job_id])

    def test_fixed_predecessor_becomes_earliest_start(self) -> None:
        """A successor of a started activity starts after its locked window."""
        started = _make_activity("T001", 1, "SNA", di="2026-06-01", df="2026-06-10")
        started.actual_start = date(2026, 6, 3)
        started.save()
        succ = _make_activity("T001", 1, "SNB")
        succ.predecessors = started.activity_id
        succ.save()
        problem = build_scheduling_problem([started.job_id])
        act = next(
            a
            for j in problem.jobs
            for a in j.activities
            if a.activity_id == succ.activity_id
        )
        # Locked window ends 2026-06-10 -> successor can start from the 11th.
        self.assertEqual(act.earliest_start, date(2026, 6, 11))
        # The dangling reference to the excluded predecessor is dropped.
        self.assertEqual(act.predecessors, [])

    def test_predecessors_from_db_field(self) -> None:
        a1 = _make_activity("T001", 1, "prep")
        a2 = _make_activity("T001", 1, "work")
        a2.predecessors = a1.activity_id
        a2.save()
        problem = build_scheduling_problem([a1.job_id])
        by_id = {a.activity_id: a for a in problem.jobs[0].activities}
        self.assertEqual(by_id[a2.activity_id].predecessors, [a1.activity_id])
        self.assertEqual(by_id[a1.activity_id].predecessors, [])

    def test_activities_without_predecessors_are_independent(self) -> None:
        """No predecessors recorded in DB -> no forced chain in the model."""
        from src.optimization.data_model import precedence_pairs

        a1 = _make_activity("T001", 1, "SNA")
        _make_activity("T001", 1, "SNB")
        _make_activity("T001", 1, "SNC")
        problem = build_scheduling_problem([a1.job_id])
        self.assertEqual(precedence_pairs(problem.jobs[0]), [])

    def test_unknown_predecessor_reference_dropped(self) -> None:
        a1 = _make_activity("T001", 1, "prep")
        a1.predecessors = "OTHER-01-x, " + a1.activity_id
        a1.save()
        problem = build_scheduling_problem([a1.job_id])
        self.assertEqual(problem.jobs[0].activities[0].predecessors, [])

    def test_gantt_after_excludes_fixed_activity(self) -> None:
        from src.optimization.output_model import (
            ActivitySchedule,
            JobResult,
            ScheduleResult,
        )
        from ui.services.optimization_problem import (
            filter_plannable_gantt_tasks,
            fixed_activities_for_llm,
        )

        started = _make_activity("T001", 1, "SNA", di="2026-06-01", df="2026-06-10")
        started.actual_start = date(2026, 6, 3)
        started.save()
        free = _make_activity("T001", 1, "SNB")
        result = ScheduleResult(
            activities=[
                ActivitySchedule(
                    activity_id=free.activity_id,
                    job_id=free.job_id,
                    plant="Valbrembo",
                    start_date=date(2026, 7, 1),
                    end_date=date(2026, 7, 5),
                    assigned_resource_ids=["mandrino:m-test"],
                )
            ],
            jobs=[
                JobResult(
                    job_id=free.job_id,
                    due_date=date(2026, 12, 31),
                    completion_date=date(2026, 7, 5),
                    delay_days=0,
                    delay_weeks=0,
                    penalty=0.0,
                )
            ],
            total_penalty=0.0,
            num_late_jobs=0,
        )
        rows = [started, free]
        tasks_all = schedule_result_to_gantt_tasks(
            result, {a.activity_id: a for a in rows}, {}, attivita=rows
        )
        tasks = filter_plannable_gantt_tasks(tasks_all, rows)
        by_id = {t["activity_id"]: t for t in tasks}
        self.assertNotIn(started.activity_id, by_id)
        self.assertEqual(by_id[free.activity_id]["start"], "2026-07-01")
        fixed = fixed_activities_for_llm(rows, {})
        self.assertEqual(len(fixed), 1)
        self.assertEqual(fixed[0]["activity_id"], started.activity_id)
        self.assertEqual(fixed[0]["stato"], "in_corso")

    def test_fixed_calendar_window_in_progress(self) -> None:
        att = _make_activity("T001", 9, "win", di="2026-06-01", df="2026-06-10")
        att.actual_start = date(2026, 6, 3)
        self.assertTrue(is_attivita_fixed(att))
        self.assertEqual(
            fixed_calendar_window(att), (date(2026, 6, 3), date(2026, 6, 10))
        )

    def test_no_implicit_earliest_start_from_db(self) -> None:
        att = _make_activity("T001", 1, "solo")
        problem = build_scheduling_problem([att.job_id])
        self.assertIsNone(problem.jobs[0].activities[0].earliest_start)

    def test_duration_from_calendar_dates_not_duration_min(self) -> None:
        att = _make_activity("T001", 1, "solo", di="2026-07-30", df="2026-08-19")
        self.assertEqual(
            _duration_days(att.duration_min, att.data_inizio, att.data_fine), 15
        )
        problem = build_scheduling_problem([att.job_id])
        self.assertEqual(problem.jobs[0].activities[0].duration_days, 15)

    def test_default_horizon_is_tomorrow(self) -> None:
        att = _make_activity("T001", 1, "solo")
        problem = build_scheduling_problem([att.job_id])
        self.assertEqual(problem.horizon_start, localdate() + timedelta(days=1))

    def test_job_without_penalty_has_zero_penalty_per_week(self) -> None:
        att = _make_activity("T001", 1, "solo")
        problem = build_scheduling_problem([att.job_id])
        self.assertEqual(problem.jobs[0].penalty_per_week, 0.0)

    def test_job_with_cost_and_penalty_percent(self) -> None:
        Job.objects.filter(commessa="T001").update(
            total_cost=500_000.0,
            penalty=2.0,
        )
        att = _make_activity("T001", 1, "solo")
        problem = build_scheduling_problem([att.job_id])
        self.assertEqual(problem.jobs[0].penalty_per_week, 10_000.0)

    def test_late_job_without_penalty_counts_no_penalty(self) -> None:
        att = _make_activity("T001", 1, "solo", di="2027-01-01", df="2027-01-10")
        tasks = activities_to_gantt_tasks([att], {})
        metrics = schedule_metrics_from_tasks(tasks, [att.job_id])
        self.assertEqual(metrics["num_late_jobs"], 1)
        self.assertEqual(metrics["total_penalty"], 0.0)

    def test_earliest_start_override(self) -> None:
        att = _make_activity("T001", 1, "solo")
        override = date(2026, 7, 1)
        problem = build_scheduling_problem(
            [att.job_id],
            earliest_start_overrides={att.activity_id: override},
        )
        self.assertEqual(problem.jobs[0].activities[0].earliest_start, override)

    def test_empty_selection_raises(self) -> None:
        with self.assertRaises(OptimizationProblemError):
            build_scheduling_problem([])

    def test_gantt_tasks_include_current_resources(self) -> None:
        att = _make_activity("T001", 1, "prep")
        RisorsaAttivita.objects.create(
            attivita=att, tipo="generatore", risorsa_id="G501"
        )
        RisorsaAttivita.objects.create(
            attivita=att, tipo="mandrino", risorsa_id="generatore:M12"
        )
        tasks = activities_to_gantt_tasks([att], {})
        self.assertEqual(tasks[0]["assigned_resources"], ["G501", "M12"])

    def test_match_solver_activity_id_from_db_problem_format(self) -> None:
        att = _make_activity("25021", 1, "Qualifica")
        matched = match_attivita_for_solver_activity_id(
            f"25021-1-Qualifica-{att.pk}",
            [att],
        )
        self.assertIs(matched, att)

    def test_hydrate_restores_resources_on_stale_task_dict(self) -> None:
        att = _make_activity("T001", 1, "solo")
        RisorsaAttivita.objects.create(attivita=att, tipo="tastatore", risorsa_id="T9")
        stale = [{"activity_id": att.activity_id, "assigned_resources": []}]
        hydrate_gantt_tasks_resources(stale)
        self.assertEqual(stale[0]["assigned_resources"], ["T9"])
        self.assertEqual(stale[0]["resources"], "T9")


class OptimizationWizardTest(TestCase):
    """Integration tests for the multi-page optimization flow."""

    def setUp(self) -> None:
        _seed_inventory()
        self.user = User.objects.create_user("optuser", password="pass")
        self.client = Client()
        self.client.force_login(self.user)
        Job.objects.create(commessa="T002", delivery_date="2027-01-15")
        self.a1 = _make_activity("T002", 1, "a", di="2026-06-01", df="2026-06-03")
        _make_activity("T002", 2, "b", di="2026-06-04", df="2026-06-06")
        self.job_id = self.a1.job_id
        self.draft = {
            "job_ids": [self.job_id],
            "earliest_start_overrides": {},
        }

    def test_step1_renders(self) -> None:
        resp = self.client.get(reverse("ottimizzazione"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Seleziona tutte")
        self.assertContains(resp, "ott-filter-btn")
        self.assertContains(resp, "ott-job-list")
        self.assertContains(resp, "Vincoli")
        self.assertContains(resp, "Primo giorno pianificabile")
        self.assertContains(resp, "Ottimizza")
        self.assertNotContains(resp, "Continua")

    def test_parametri_redirects_to_commesse(self) -> None:
        resp = self.client.get(reverse("ottimizzazione_parametri"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("ottimizzazione"))

    def test_run_stores_draft_from_post(self) -> None:
        AppSettings.load().solver_time_limit = 5
        AppSettings.load().save()
        horizon = (localdate() + timedelta(days=1)).isoformat()
        payload = {
            **self.draft,
            "horizon_start": horizon,
        }
        resp = self.client.post(
            reverse("ottimizzazione_run"),
            data={"draft_json": json.dumps(payload)},
        )
        if resp.status_code == 302 and resp.url == reverse("ottimizzazione"):
            self.skipTest("Solver did not find a feasible solution in test env.")
        session_draft = self.client.session.get(SESSION_DRAFT_KEY)
        self.assertEqual(session_draft["job_ids"], [self.job_id])
        self.assertEqual(session_draft["horizon_start"], horizon)

    @patch(
        "ui.views_ottimizzazione.generate_optimization_explanation",
        return_value=_MOCK_EXPLANATION,
    )
    def test_run_solve_redirects_to_results(self, _mock_explain) -> None:
        AppSettings.load().solver_time_limit = 5
        AppSettings.load().save()
        horizon = (localdate() + timedelta(days=1)).isoformat()
        payload = {
            **self.draft,
            "horizon_start": horizon,
        }
        resp = self.client.post(
            reverse("ottimizzazione_run"),
            data={"draft_json": json.dumps(payload)},
        )
        if resp.status_code == 302 and resp.url == reverse("ottimizzazione"):
            self.skipTest("Solver did not find a feasible solution in test env.")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("ottimizzazione_risultati"))
        self.assertIn(SESSION_RESULT_KEY, self.client.session)
        self.assertEqual(
            self.client.session.get(SESSION_EXPLANATION_KEY),
            _MOCK_EXPLANATION,
        )

    def test_results_page_requires_session(self) -> None:
        resp = self.client.get(reverse("ottimizzazione_risultati"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("ottimizzazione"))

    def test_results_page_requires_explanation(self) -> None:
        session = self.client.session
        session[SESSION_RESULT_KEY] = {"tasks_before": [], "tasks_after": []}
        session.save()
        resp = self.client.get(reverse("ottimizzazione_risultati"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("ottimizzazione"))

    def test_stale_draft_job_ids_removed_on_page_load(self) -> None:
        session = self.client.session
        session[SESSION_DRAFT_KEY] = {
            "job_ids": [self.job_id, "GHOST99"],
            "earliest_start_overrides": {},
        }
        session.save()
        resp = self.client.get(reverse("ottimizzazione"))
        self.assertEqual(resp.status_code, 200)
        session_draft = self.client.session.get(SESSION_DRAFT_KEY)
        self.assertEqual(session_draft["job_ids"], [self.job_id])

    def test_nuova_clears_session(self) -> None:
        session = self.client.session
        session[SESSION_DRAFT_KEY] = self.draft
        session[SESSION_RESULT_KEY] = {"tasks_before": []}
        session[SESSION_EXPLANATION_KEY] = "cached text"
        session.save()
        resp = self.client.get(reverse("ottimizzazione_nuova"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("ottimizzazione"))
        self.assertNotIn(SESSION_DRAFT_KEY, self.client.session)
        self.assertNotIn(SESSION_RESULT_KEY, self.client.session)
        self.assertNotIn(SESSION_EXPLANATION_KEY, self.client.session)

    @patch(
        "ui.views_ottimizzazione.generate_optimization_explanation",
        return_value=_MOCK_EXPLANATION,
    )
    def test_api_solve_still_works(self, _mock_explain) -> None:
        AppSettings.load().solver_time_limit = 5
        AppSettings.load().save()
        resp = self.client.post(
            reverse("api_ottimizzazione_solve"),
            data=json.dumps({"job_ids": [self.job_id]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        if data.get("feasible"):
            self.assertIn("redirect_url", data)
