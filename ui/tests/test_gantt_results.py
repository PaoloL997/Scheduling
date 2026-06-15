"""Tests for optimization results Gantt rendering."""

from __future__ import annotations

import json
import shutil
from unittest.mock import patch
import subprocess
from datetime import date, timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.timezone import localdate

from ui.models import AppSettings, Attivita, Job, MandrinoItem, TastatoreItem
from ui.views_ottimizzazione import SESSION_RESULT_KEY


def _seed() -> Attivita:
    MandrinoItem.objects.get_or_create(
        uid="m-gantt", defaults={"diameter": "15", "plant": "Valbrembo"}
    )
    TastatoreItem.objects.get_or_create(
        uid="t-gantt", defaults={"diameter": "15", "plant": "Valbrembo"}
    )
    Job.objects.create(commessa="TG01", delivery_date="2027-06-30")
    att = Attivita(
        commessa="TG01",
        item=1,
        activity="prep",
        data_inizio=date(2026, 6, 1),
        data_fine=date(2026, 6, 8),
        duration_min=480,
        stabilimento="Valbrembo",
    )
    att.save()
    _make_activity = Attivita(
        commessa="TG01",
        item=2,
        activity="work",
        data_inizio=date(2026, 6, 9),
        data_fine=date(2026, 6, 15),
        duration_min=480,
        stabilimento="Valbrembo",
    )
    _make_activity.save()
    return att


class GanttResultsPageTest(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("ganttuser", password="pass")
        self.client = Client()
        self.client.force_login(self.user)
        self.att = _seed()
        AppSettings.load().solver_time_limit = 5
        AppSettings.load().save()

    def _run_to_results(self) -> dict | None:
        horizon = (localdate() + timedelta(days=1)).isoformat()
        payload = {
            "job_ids": [self.att.job_id],
            "earliest_start_overrides": {},
            "horizon_start": horizon,
        }
        with patch(
            "ui.views_ottimizzazione.generate_optimization_explanation",
            return_value="Spiegazione di test.",
        ):
            resp = self.client.post(
                reverse("ottimizzazione_run"),
                data={"draft_json": json.dumps(payload)},
            )
        if resp.status_code == 302 and resp.url == reverse("ottimizzazione"):
            return None
        return self.client.session.get(SESSION_RESULT_KEY)

    def test_results_page_includes_gantt_tasks_json(self) -> None:
        result = self._run_to_results()
        if result is None:
            self.skipTest("Solver did not find a feasible solution in test env.")
        self.assertTrue(result.get("tasks_before"))
        self.assertTrue(result.get("tasks_after"))

        resp = self.client.get(reverse("ottimizzazione_risultati"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("gantt-compare-bars-svg", html)
        self.assertIn("compareMode: true", html)
        self.assertNotIn("gantt-before-bars-svg", html)
        self.assertIn("GanttChart.computeFitViewport", html)
        self.assertIn('"start"', html)
        self.assertIn("tasks_before", html)
        self.assertIn("renderWhenReady", html)
        self.assertIn("ott-res-table", html)
        self.assertIn("renderTable", html)

    def test_gantt_fit_renders_task_bars_in_node(self) -> None:
        if not shutil.which("node"):
            self.skipTest("Node.js not available")
        script = Path(__file__).resolve().parent / "fixtures" / "gantt_fit_test.mjs"
        if not script.is_file():
            self.fail(f"Missing gantt test script: {script}")
        root = Path(__file__).resolve().parents[2]
        if not (root / "node_modules" / "jsdom").is_dir():
            self.skipTest("Run `npm install --no-save jsdom` to enable Gantt JS tests")
        proc = subprocess.run(
            ["node", str(script)],
            capture_output=True,
            text=True,
            cwd=root,
            check=False,
        )
        if proc.returncode != 0:
            self.fail(
                "gantt_fit_test.mjs failed:\n"
                + (proc.stderr or proc.stdout or "unknown error")
            )
        self.assertIn("ok", proc.stdout)

    def test_gantt_actual_window_draws_ticks(self) -> None:
        if not shutil.which("node"):
            self.skipTest("Node.js not available")
        script = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "gantt_actual_window_test.mjs"
        )
        if not script.is_file():
            self.fail(f"Missing gantt test script: {script}")
        root = Path(__file__).resolve().parents[2]
        if not (root / "node_modules" / "jsdom").is_dir():
            self.skipTest("Run `npm install --no-save jsdom` to enable Gantt JS tests")
        proc = subprocess.run(
            ["node", str(script)],
            capture_output=True,
            text=True,
            cwd=root,
            check=False,
        )
        if proc.returncode != 0:
            self.fail(
                "gantt_actual_window_test.mjs failed:\n"
                + (proc.stderr or proc.stdout or "unknown error")
            )
        self.assertIn("ok", proc.stdout)

    def test_gantt_badges_follow_latest_end_rule(self) -> None:
        if not shutil.which("node"):
            self.skipTest("Node.js not available")
        script = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "gantt_badge_anchor_rules_test.mjs"
        )
        if not script.is_file():
            self.fail(f"Missing gantt test script: {script}")
        root = Path(__file__).resolve().parents[2]
        if not (root / "node_modules" / "jsdom").is_dir():
            self.skipTest("Run `npm install --no-save jsdom` to enable Gantt JS tests")
        proc = subprocess.run(
            ["node", str(script)],
            capture_output=True,
            text=True,
            cwd=root,
            check=False,
        )
        if proc.returncode != 0:
            self.fail(
                "gantt_badge_anchor_rules_test.mjs failed:\n"
                + (proc.stderr or proc.stdout or "unknown error")
            )
        self.assertIn("ok", proc.stdout)

    def test_gantt_badges_anchor_after_in_progress_actual_window(self) -> None:
        if not shutil.which("node"):
            self.skipTest("Node.js not available")
        script = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "gantt_badge_inprogress_test.mjs"
        )
        if not script.is_file():
            self.fail(f"Missing gantt test script: {script}")
        root = Path(__file__).resolve().parents[2]
        if not (root / "node_modules" / "jsdom").is_dir():
            self.skipTest("Run `npm install --no-save jsdom` to enable Gantt JS tests")
        proc = subprocess.run(
            ["node", str(script)],
            capture_output=True,
            text=True,
            cwd=root,
            check=False,
        )
        if proc.returncode != 0:
            self.fail(
                "gantt_badge_inprogress_test.mjs failed:\n"
                + (proc.stderr or proc.stdout or "unknown error")
            )
        self.assertIn("ok", proc.stdout)

    def test_gantt_badges_anchor_after_actual_finish(self) -> None:
        if not shutil.which("node"):
            self.skipTest("Node.js not available")
        script = (
            Path(__file__).resolve().parent / "fixtures" / "gantt_badge_anchor_test.mjs"
        )
        if not script.is_file():
            self.fail(f"Missing gantt test script: {script}")
        root = Path(__file__).resolve().parents[2]
        if not (root / "node_modules" / "jsdom").is_dir():
            self.skipTest("Run `npm install --no-save jsdom` to enable Gantt JS tests")
        proc = subprocess.run(
            ["node", str(script)],
            capture_output=True,
            text=True,
            cwd=root,
            check=False,
        )
        if proc.returncode != 0:
            self.fail(
                "gantt_badge_anchor_test.mjs failed:\n"
                + (proc.stderr or proc.stdout or "unknown error")
            )
        self.assertIn("ok", proc.stdout)

    def test_gantt_badges_anchor_after_completed_early(self) -> None:
        if not shutil.which("node"):
            self.skipTest("Node.js not available")
        script = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "gantt_badge_completed_test.mjs"
        )
        if not script.is_file():
            self.fail(f"Missing gantt test script: {script}")
        root = Path(__file__).resolve().parents[2]
        if not (root / "node_modules" / "jsdom").is_dir():
            self.skipTest("Run `npm install --no-save jsdom` to enable Gantt JS tests")
        proc = subprocess.run(
            ["node", str(script)],
            capture_output=True,
            text=True,
            cwd=root,
            check=False,
        )
        if proc.returncode != 0:
            self.fail(
                "gantt_badge_completed_test.mjs failed:\n"
                + (proc.stderr or proc.stdout or "unknown error")
            )
        self.assertIn("ok", proc.stdout)

    def test_gantt_width_stable_with_scrollbar(self) -> None:
        if not shutil.which("node"):
            self.skipTest("Node.js not available")
        script = (
            Path(__file__).resolve().parent / "fixtures" / "gantt_stable_width_test.mjs"
        )
        if not script.is_file():
            self.fail(f"Missing gantt test script: {script}")
        root = Path(__file__).resolve().parents[2]
        if not (root / "node_modules" / "jsdom").is_dir():
            self.skipTest("Run `npm install --no-save jsdom` to enable Gantt JS tests")
        proc = subprocess.run(
            ["node", str(script)],
            capture_output=True,
            text=True,
            cwd=root,
            check=False,
        )
        if proc.returncode != 0:
            self.fail(
                "gantt_stable_width_test.mjs failed:\n"
                + (proc.stderr or proc.stdout or "unknown error")
            )
        self.assertIn("ok", proc.stdout)
