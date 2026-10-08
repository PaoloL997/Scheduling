"""Tests for the pianificazione job header metrics."""

import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.timezone import localdate

from ui.models import Attivita, Job, ProblemaAttivita, RisorsaAttivita, TestaItem
from ui.services.planning_read import count_job_open_problems, fetch_grouped
from ui.services.problem_detection import detect_for_all_activities


class PianificazioneProgressBarTests(TestCase):
    """Ensure the completion progress bar is always rendered in job headers."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(username="planner", password="p")
        self.client.force_login(self.user)
        Job.objects.create(commessa="C001", job_id="C001")
        Attivita.objects.create(
            commessa="C001",
            job_id="C001",
            item=1,
            activity_id="C001-01-A1",
            activity="A1",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
        )

    def test_progress_bar_rendered_with_zero_completion(self) -> None:
        resp = self.client.get(reverse("pianificazione"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('class="pian-head-progress"', html)
        self.assertIn('class="pian-progress-track"', html)
        self.assertIn("data-job-progress-fill", html)
        self.assertIn("data-job-progress-pct", html)
        self.assertIn("Completamento", html)
        self.assertIn("function _countWorkingDaysInclusive(", html)
        self.assertIn("function _activityWorkProgress(", html)
        self.assertIn("stats.progressPct", html)

    def test_progress_bar_still_rendered_for_in_progress_activity(self) -> None:
        """In-progress activities (actual_start only) still render the progress bar."""
        att = Attivita.objects.get(commessa="C001")
        att.actual_start = localdate() - timedelta(days=2)
        att.save(update_fields=["actual_start"])

        resp = self.client.get(reverse("pianificazione"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('data-actual-start="', html)
        self.assertIn('class="pian-head-progress"', html)
        self.assertIn("data-job-progress-fill", html)
        self.assertIn("_activityWorkProgress(", html)

    def test_open_problems_count_in_job_header(self) -> None:
        att = Attivita.objects.get(commessa="C001")
        ProblemaAttivita.objects.create(
            attivita=att,
            tipo="acquisto",
            risorsa_tipo="testa",
            risorsa_uid="T001",
            descrizione="Testa mancante",
            risolto=False,
        )
        ProblemaAttivita.objects.create(
            attivita=att,
            tipo="spostamento",
            risorsa_tipo="generatore",
            risorsa_uid="G001",
            descrizione="Generatore da spostare",
            risolto=False,
        )
        resp = self.client.get(reverse("pianificazione"))
        self.assertContains(resp, "pian-stat-item--alerts is-active")
        # Loading the page reconciles problems (stale ones close, shortfalls and
        # ritardo notes open), so assert the header matches the resulting state
        # rather than a fixed number.
        expected = ProblemaAttivita.objects.filter(attivita=att, risolto=False).count()
        self.assertGreaterEqual(expected, 2)
        self.assertContains(resp, f"<strong data-job-alerts>{expected}</strong>")

    def test_job_header_counts_unresolved_overlap_as_problem(self) -> None:
        """Overlap flags only the not-started activity via warning_count."""
        late = Attivita.objects.create(
            commessa="C101",
            job_id="C101",
            item=1,
            activity_id="C101-1-Saldatura",
            activity="Saldatura",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 10),
            data_fine=date(2026, 8, 19),
            actual_start=date(2026, 8, 17),
        )
        blocked = Attivita.objects.create(
            commessa="C205",
            job_id="C205",
            item=2,
            activity_id="C205-2-Montaggio",
            activity="Montaggio",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 20),
            data_fine=date(2026, 8, 29),
        )
        for att in (late, blocked):
            RisorsaAttivita.objects.create(
                attivita=att, tipo="testa", risorsa_id="OV-T1"
            )
        TestaItem.objects.create(uid="OV-T1", model="TP60", plant="Valbrembo")
        # The activities above already created their Job rows.
        self.assertTrue(Job.objects.filter(commessa__in=["C101", "C205"]).count() == 2)

        # Reading never writes: reconcile explicitly, as the write paths do.
        detect_for_all_activities()
        grouped = {g["job"]["commessa"]: g for g in fetch_grouped()}

        # Job headers still sum every unresolved problem into warning_count.
        for commessa in ("C101", "C205"):
            acts = grouped[commessa]["activities"]
            warnings = sum(a.get("warning_count") or 0 for a in acts)
            self.assertEqual(grouped[commessa]["job"]["n_open_problems"], warnings)

        # The overlap (sovrapposizione) problem is attached to the not-started
        # activity only; the started peer reserves the resource but is unflagged.
        self.assertTrue(
            ProblemaAttivita.objects.filter(
                attivita=blocked,
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
                risolto=False,
            ).exists()
        )
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=late,
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
                risolto=False,
            ).exists()
        )

    def test_count_job_open_problems_sums_warning_count(self) -> None:
        self.assertEqual(
            count_job_open_problems(
                [
                    {"orm_id": 1, "warning_count": 2},
                    {"orm_id": 2, "warning_count": 1},
                ]
            ),
            3,
        )


class NotesModalScriptTest(TestCase):
    """The notes modal renders the reschedule proposal and sorts open first."""

    def test_notes_modal_script_in_node(self) -> None:
        if not shutil.which("node"):
            self.skipTest("Node.js not available")
        script = Path(__file__).resolve().parent / "fixtures" / "notes_modal_test.mjs"
        if not script.is_file():
            self.fail(f"Missing modal test script: {script}")
        root = Path(__file__).resolve().parents[2]
        if not (root / "node_modules" / "jsdom").is_dir():
            self.skipTest("Run `npm install --no-save jsdom` to enable JS tests")
        proc = subprocess.run(
            ["node", str(script)],
            capture_output=True,
            text=True,
            cwd=root,
            check=False,
        )
        if proc.returncode != 0:
            self.fail(
                "notes_modal_test.mjs failed:\n"
                + (proc.stderr or proc.stdout or "unknown error")
            )
        self.assertIn("ok", proc.stdout)
