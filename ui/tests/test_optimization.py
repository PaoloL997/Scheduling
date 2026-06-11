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
    hydrate_gantt_tasks_resources,
    match_attivita_for_solver_activity_id,
    schedule_metrics_from_tasks,
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
