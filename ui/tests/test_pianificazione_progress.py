"""Tests for the pianificazione job header metrics."""

from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.timezone import localdate

from ui.models import Attivita, Job, ProblemaAttivita


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
        self.assertContains(resp, "<strong data-job-alerts>2</strong>")
