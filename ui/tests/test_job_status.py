"""Tests for commessa aggregate status, sorting, and optimization exclusion."""

from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth.models import User
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils.timezone import localdate

from ui.models import Attivita, Job
from ui.views import (
    _aggregate_job_status,
    _fetch_grouped,
    _has_plannable_activities,
    _is_job_completed,
    pianificazione,
)
from ui.views_ottimizzazione import (
    SESSION_DRAFT_KEY,
    _optimization_groups,
    _sanitize_draft,
    ottimizzazione,
)


def _make_att(
    commessa: str,
    *,
    di: date | None = None,
    df: date | None = None,
    actual_start: date | None = None,
    actual_finish: date | None = None,
    item: int = 1,
    activity: str = "A1",
) -> Attivita:
    return Attivita.objects.create(
        commessa=commessa,
        job_id=commessa,
        item=item,
        activity_id=f"{commessa}-{item:02d}-{activity}",
        activity=activity,
        data_inizio=di,
        data_fine=df,
        actual_start=actual_start,
        actual_finish=actual_finish,
        duration_min=480,
    )


def _request_with_session(factory: RequestFactory, path: str, user: User):
    request = factory.get(path)
    request.user = user
    middleware = SessionMiddleware(lambda r: None)
    middleware.process_request(request)
    request.session.save()
    return request


class JobStatusHelpersTest(TestCase):
    """Unit tests for aggregate status and completion helpers."""

    def test_is_job_completed_requires_all_activities_closed(self) -> None:
        tasks = [
            {"actual_start": "2026-01-01", "actual_finish": "2026-01-02"},
            {"actual_start": "2026-01-03", "actual_finish": "2026-01-04"},
        ]
        self.assertTrue(_is_job_completed(tasks))

        tasks[1]["actual_finish"] = None
        self.assertFalse(_is_job_completed(tasks))
        self.assertFalse(_is_job_completed([]))

    def test_has_plannable_activities(self) -> None:
        today = localdate().isoformat()
        self.assertTrue(
            _has_plannable_activities([{"actual_start": None, "actual_finish": None}])
        )
        self.assertFalse(
            _has_plannable_activities([{"actual_start": today, "actual_finish": None}])
        )
        self.assertFalse(
            _has_plannable_activities([{"actual_start": today, "actual_finish": today}])
        )
        self.assertTrue(
            _has_plannable_activities(
                [
                    {"actual_start": today, "actual_finish": None},
                    {"actual_start": None, "actual_finish": None},
                ]
            )
        )

    def test_aggregate_job_status_completed_on_time(self) -> None:
        today = localdate()
        tasks = [
            {
                "status_pills": [{"key": "completata", "label": "COMPLETATA"}],
                "start": today.isoformat(),
                "finish": (today + timedelta(days=2)).isoformat(),
                "actual_start": today.isoformat(),
                "actual_finish": (today + timedelta(days=1)).isoformat(),
            }
        ]
        self.assertEqual(_aggregate_job_status(tasks), "in_tempo")

    def test_aggregate_job_status_late_over_planned(self) -> None:
        tasks = [
            {"status_pills": [{"key": "in_ritardo", "label": "IN RITARDO"}]},
            {"status_pills": [{"key": "pianificata", "label": "PIANIFICATA"}]},
        ]
        self.assertEqual(_aggregate_job_status(tasks), "in_ritardo")

    def test_aggregate_job_status_in_tempo(self) -> None:
        today = localdate()
        tasks = [
            {
                "status_pills": [{"key": "in_lavorazione", "label": "IN LAVORAZIONE"}],
                "start": today.isoformat(),
                "finish": (today + timedelta(days=5)).isoformat(),
                "actual_start": today.isoformat(),
                "actual_finish": None,
            },
            {"status_pills": [{"key": "pianificata", "label": "PIANIFICATA"}]},
        ]
        self.assertEqual(_aggregate_job_status(tasks), "in_tempo")

    def test_aggregate_job_status_pianificata(self) -> None:
        future = localdate() + timedelta(days=30)
        tasks = [
            {
                "status_pills": [{"key": "pianificata", "label": "PIANIFICATA"}],
                "start": future.isoformat(),
                "finish": (future + timedelta(days=5)).isoformat(),
            }
        ]
        self.assertEqual(_aggregate_job_status(tasks), "pianificata")

    def test_aggregate_job_status_rischio_penale(self) -> None:
        start = localdate()
        tasks = [
            {
                "status_pills": [{"key": "in_lavorazione", "label": "IN LAVORAZIONE"}],
                "start": start.isoformat(),
                "finish": (start + timedelta(days=5)).isoformat(),
                "actual_start": start.isoformat(),
                "actual_finish": None,
            }
        ]
        deadline = (start + timedelta(days=2)).isoformat()
        self.assertEqual(_aggregate_job_status(tasks, deadline), "rischio_penale")


class FetchGroupedSortTest(TestCase):
    """Ensure _fetch_grouped orders commesse by earliest start date."""

    def setUp(self) -> None:
        today = localdate()
        past = today - timedelta(days=30)
        future = today + timedelta(days=30)

        Job.objects.bulk_create(
            [
                Job(commessa="Z_DONE", job_id="Z_DONE"),
                Job(commessa="A_LATE", job_id="A_LATE"),
                Job(commessa="B_WORK", job_id="B_WORK"),
                Job(commessa="C_PLAN", job_id="C_PLAN"),
            ]
        )

        _make_att(
            "Z_DONE",
            di=past + timedelta(days=2),
            df=past + timedelta(days=7),
            actual_start=past + timedelta(days=2),
            actual_finish=past + timedelta(days=7),
        )
        _make_att("A_LATE", di=past, df=past + timedelta(days=5))
        _make_att(
            "B_WORK",
            di=past + timedelta(days=1),
            df=past + timedelta(days=6),
            actual_start=past + timedelta(days=1),
        )
        _make_att("C_PLAN", di=future, df=future + timedelta(days=5))

    def test_fetch_grouped_sorts_by_earliest_start_then_name(self) -> None:
        groups = _fetch_grouped()
        names = [g["job"]["commessa"] for g in groups]
        self.assertEqual(names, ["A_LATE", "B_WORK", "Z_DONE", "C_PLAN"])

    def test_activities_within_commessa_sorted_by_start(self) -> None:
        today = localdate()
        Job.objects.create(commessa="MULTI", job_id="MULTI")
        _make_att(
            "MULTI",
            item=2,
            activity="Later",
            di=today + timedelta(days=10),
            df=today + timedelta(days=15),
        )
        _make_att(
            "MULTI",
            item=1,
            activity="Earlier",
            di=today + timedelta(days=1),
            df=today + timedelta(days=5),
        )
        groups = {g["job"]["commessa"]: g for g in _fetch_grouped()}
        acts = groups["MULTI"]["activities"]
        self.assertEqual(
            [a["activity"] for a in acts],
            ["Earlier", "Later"],
        )


class OptimizationExcludeCompletedTest(TestCase):
    """Commesse without plannable activities must not appear in optimization selection."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="opt", password="p")
        today = localdate()

        Job.objects.create(commessa="OPEN", job_id="OPEN")
        Job.objects.create(commessa="DONE", job_id="DONE")
        Job.objects.create(commessa="WORKING", job_id="WORKING")
        Job.objects.create(commessa="MIXED", job_id="MIXED")

        _make_att("OPEN", di=today, df=today + timedelta(days=5))
        _make_att(
            "DONE",
            di=today,
            df=today + timedelta(days=5),
            actual_start=today,
            actual_finish=today + timedelta(days=1),
        )
        _make_att(
            "WORKING",
            di=today,
            df=today + timedelta(days=5),
            actual_start=today,
        )
        _make_att("MIXED", di=today, df=today + timedelta(days=5))
        _make_att(
            "MIXED",
            item=2,
            di=today,
            df=today + timedelta(days=5),
            actual_start=today,
        )

    def test_optimization_groups_excludes_completed(self) -> None:
        groups = _optimization_groups()
        commesse = {g["job"]["commessa"] for g in groups}
        self.assertIn("OPEN", commesse)
        self.assertIn("MIXED", commesse)
        self.assertNotIn("DONE", commesse)
        self.assertNotIn("WORKING", commesse)

    def test_sanitize_draft_drops_completed_job_ids(self) -> None:
        draft = {
            "job_ids": ["OPEN", "DONE", "WORKING", "MIXED"],
            "earliest_start_overrides": {},
            "horizon_start": None,
        }
        cleaned = _sanitize_draft(draft)
        self.assertEqual(sorted(cleaned["job_ids"]), ["MIXED", "OPEN"])

    def test_ottimizzazione_view_prunes_completed_from_session_draft(self) -> None:
        request = _request_with_session(
            self.factory, reverse("ottimizzazione"), self.user
        )
        request.session[SESSION_DRAFT_KEY] = {
            "job_ids": ["OPEN", "DONE", "WORKING"],
            "earliest_start_overrides": {},
            "horizon_start": None,
        }
        request.session.save()

        response = ottimizzazione(request)
        self.assertEqual(response.status_code, 200)
        stored = request.session.get(SESSION_DRAFT_KEY, {})
        self.assertEqual(stored.get("job_ids"), ["OPEN"])


class PianificazioneFilterUiTest(TestCase):
    """Template includes status filter controls."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="plan", password="p")
        Job.objects.create(commessa="C001", job_id="C001")

    def test_pianificazione_has_status_filter_button(self) -> None:
        request = self.factory.get(reverse("pianificazione"))
        request.user = self.user
        response = pianificazione(request)
        html = response.content.decode()
        self.assertIn('id="pian-filter-btn"', html)
        self.assertIn('id="pian-filter-panel"', html)
        self.assertIn('id="pian-filter-rischio_penale"', html)
        self.assertIn('id="pian-filter-in_ritardo"', html)
        self.assertIn('id="pian-filter-in_tempo"', html)
        self.assertIn('id="pian-filter-pianificata"', html)
        self.assertNotIn('id="pian-filter-completata"', html)
        self.assertNotIn('id="pian-filter-in_lavorazione"', html)
