"""Tests for never-started activities that are past their planned finish.

Reproduces the reported case: commessa 25078 where the qualifica had to be
closed late while the production activity 02-SNC — never started and already
past its planned finish — still held the same testa H14.
"""

from __future__ import annotations

import json
from datetime import date

from django.test import RequestFactory, TestCase

from ui.models import Attivita, ProblemaAttivita, RisorsaAttivita, TestaItem
from ui.services.bridge_write import BridgeWriteError, update_attivita
from ui.services.problem_detection import (
    find_resource_overlaps,
    overdue_not_started,
    sync_overdue_problems,
)
from ui.services.resource_conflicts import occupation_window
from ui.tests.helpers import write_user
from ui.views import _compute_activity_status, api_tasks_update

TODAY = date(2026, 9, 4)


def _activity(commessa, item, activity, start, finish, **kwargs):
    """Create an activity with planned dates given as ISO strings."""
    return Attivita.objects.create(
        commessa=commessa,
        item=item,
        activity=activity,
        data_inizio=date.fromisoformat(start),
        data_fine=date.fromisoformat(finish),
        stabilimento="Valbrembo",
        **kwargs,
    )


class OverdueNotStartedDetectionTest(TestCase):
    """``ritardo`` notes track never-started, past-due activities."""

    def setUp(self) -> None:
        self.att = _activity(
            "25078", "02-SNC", "produzione", "2026-08-25", "2026-09-01"
        )

    def test_predicate_true_when_past_due_and_never_started(self) -> None:
        self.assertTrue(overdue_not_started(self.att, TODAY))

    def test_predicate_false_when_started(self) -> None:
        self.att.actual_start = date(2026, 8, 26)
        self.assertFalse(overdue_not_started(self.att, TODAY))

    def test_predicate_false_before_planned_finish(self) -> None:
        self.assertFalse(overdue_not_started(self.att, date(2026, 8, 30)))

    def test_creates_open_problem(self) -> None:
        created = sync_overdue_problems([self.att], TODAY)
        self.assertEqual(created, 1)
        problema = ProblemaAttivita.objects.get(
            attivita=self.att, tipo=ProblemaAttivita.TIPO_RITARDO
        )
        self.assertFalse(problema.risolto)
        self.assertEqual(problema.bloccata_fino, date(2026, 9, 1))
        self.assertIn("01/09/2026", problema.descrizione)

    def test_is_idempotent(self) -> None:
        sync_overdue_problems([self.att], TODAY)
        self.assertEqual(sync_overdue_problems([self.att], TODAY), 0)
        self.assertEqual(
            ProblemaAttivita.objects.filter(
                tipo=ProblemaAttivita.TIPO_RITARDO, risolto=False
            ).count(),
            1,
        )

    def test_closes_when_activity_starts(self) -> None:
        sync_overdue_problems([self.att], TODAY)
        self.att.actual_start = TODAY
        self.att.save(update_fields=["actual_start"])
        sync_overdue_problems([self.att], TODAY)
        problema = ProblemaAttivita.objects.get(
            attivita=self.att, tipo=ProblemaAttivita.TIPO_RITARDO
        )
        self.assertTrue(problema.risolto)

    def test_closes_when_rescheduled_forward(self) -> None:
        sync_overdue_problems([self.att], TODAY)
        self.att.data_inizio = date(2026, 9, 7)
        self.att.data_fine = date(2026, 9, 14)
        self.att.save(update_fields=["data_inizio", "data_fine"])
        sync_overdue_problems([self.att], TODAY)
        problema = ProblemaAttivita.objects.get(
            attivita=self.att, tipo=ProblemaAttivita.TIPO_RITARDO
        )
        self.assertTrue(problema.risolto)


class OverdueStatusPillTest(TestCase):
    """The status pill tells "not started yet" from "not started and stale"."""

    def test_expired_pill(self) -> None:
        pills = _compute_activity_status(
            date(2026, 8, 25), date(2026, 9, 1), None, None, TODAY
        )
        keys = [p["key"] for p in pills]
        self.assertIn("scaduta", keys)
        self.assertIn("in_ritardo", keys)
        self.assertNotIn("da_iniziare", keys)

    def test_plain_overdue_pill_before_planned_finish(self) -> None:
        pills = _compute_activity_status(
            date(2026, 9, 1), date(2026, 9, 10), None, None, TODAY
        )
        keys = [p["key"] for p in pills]
        self.assertIn("da_iniziare", keys)
        self.assertNotIn("scaduta", keys)

    def test_started_activity_is_not_expired(self) -> None:
        pills = _compute_activity_status(
            date(2026, 8, 25), date(2026, 9, 1), date(2026, 8, 26), None, TODAY
        )
        self.assertNotIn("scaduta", [p["key"] for p in pills])

    def test_open_activity_past_planned_finish_is_late(self) -> None:
        """Started on time but still running past the planned finish."""
        pills = _compute_activity_status(
            date(2026, 8, 25), date(2026, 9, 1), date(2026, 8, 25), None, TODAY
        )
        keys = [p["key"] for p in pills]
        self.assertIn("in_ritardo", keys)
        self.assertIn("in_lavorazione", keys)
        self.assertNotIn("scaduta", keys)

    def test_open_activity_inside_planned_window_is_not_late(self) -> None:
        pills = _compute_activity_status(
            date(2026, 9, 1), date(2026, 9, 20), date(2026, 9, 1), None, TODAY
        )
        keys = [p["key"] for p in pills]
        self.assertNotIn("in_ritardo", keys)
        self.assertIn("in_lavorazione", keys)

    def test_activity_closed_after_planned_finish_stays_completed(self) -> None:
        pills = _compute_activity_status(
            date(2026, 8, 25),
            date(2026, 9, 1),
            date(2026, 8, 25),
            date(2026, 9, 3),
            TODAY,
        )
        self.assertEqual([p["key"] for p in pills], ["completata"])


class Commessa25078ClosingTest(TestCase):
    """Closing a late activity is a fact, not a plan: it can never be refused."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = write_user()
        TestaItem.objects.create(uid="H14", model="TS2000", plant="Valbrembo")

        # Qualifica: started on time, still open, planned to end 17/08.
        self.qualifica = _activity(
            "25078",
            "02",
            "qualifica",
            "2026-07-22",
            "2026-08-17",
            modello_testa="TS2000",
            actual_start=date(2026, 7, 22),
        )
        RisorsaAttivita.objects.create(
            attivita=self.qualifica, tipo="testa", risorsa_id="H14"
        )

        # Produzione 02-SNC: never started, planned window already in the past.
        self.snc = _activity(
            "25078",
            "02-SNC",
            "produzione",
            "2026-08-25",
            "2026-09-01",
            modello_testa="TS2000",
        )
        RisorsaAttivita.objects.create(
            attivita=self.snc, tipo="testa", risorsa_id="H14"
        )

    def _put(self, body: dict):
        request = self.factory.put(
            "/fake/", data=json.dumps(body), content_type="application/json"
        )
        request.user = self.user
        return request

    def test_closing_late_is_not_refused(self) -> None:
        resp = api_tasks_update(
            self._put(
                {
                    "orm_id": self.qualifica.pk,
                    "start": "2026-07-22",
                    "finish": "2026-08-17",
                    "actual_start": "2026-07-22",
                    "actual_finish": "2026-09-04",
                }
            )
        )
        self.assertEqual(resp.status_code, 200)
        self.qualifica.refresh_from_db()
        self.assertEqual(self.qualifica.actual_finish, date(2026, 9, 4))

    def test_closing_reports_the_conflict_as_a_warning(self) -> None:
        result = update_attivita(
            self.qualifica.pk,
            {
                "start": "2026-07-22",
                "finish": "2026-08-17",
                "actual_finish": "2026-09-04",
            },
        )
        self.assertIn("warnings", result)
        self.assertEqual(result["warnings"][0]["risorsa_id"], "H14")
        self.assertIn("H14", result["warning_message"])

    def test_conflict_note_lands_on_the_activity_that_has_not_started(self) -> None:
        update_attivita(
            self.qualifica.pk,
            {
                "start": "2026-07-22",
                "finish": "2026-08-17",
                "actual_finish": "2026-09-04",
            },
        )
        self.assertTrue(
            ProblemaAttivita.objects.filter(
                attivita=self.snc,
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
                risolto=False,
            ).exists()
        )
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.qualifica,
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
                risolto=False,
            ).exists()
        )

    def test_echoing_unchanged_planned_dates_never_blocks(self) -> None:
        """The panel always re-sends start/finish; that must not read as a re-plan."""
        resp = api_tasks_update(
            self._put(
                {
                    "orm_id": self.qualifica.pk,
                    "start": "2026-07-22",
                    "finish": "2026-08-17",
                    "responsible": "Rossi",
                }
            )
        )
        self.assertEqual(resp.status_code, 200)

    def test_moving_planned_dates_into_a_conflict_is_still_refused(self) -> None:
        with self.assertRaises(BridgeWriteError) as ctx:
            update_attivita(
                self.qualifica.pk,
                {"start": "2026-07-22", "finish": "2026-08-27"},
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_actual_finish_before_actual_start_is_refused(self) -> None:
        with self.assertRaises(BridgeWriteError) as ctx:
            update_attivita(
                self.qualifica.pk,
                {"actual_start": "2026-07-22", "actual_finish": "2026-07-01"},
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_malformed_date_is_refused_not_silently_cleared(self) -> None:
        with self.assertRaises(BridgeWriteError) as ctx:
            update_attivita(self.qualifica.pk, {"actual_finish": "04/09/2026"})
        self.assertEqual(ctx.exception.status_code, 400)
        self.qualifica.refresh_from_db()
        self.assertIsNone(self.qualifica.actual_finish)


class OccupancyIsOneDefinitionTest(TestCase):
    """``occupation_window`` is the only rule; the overlap engine follows it."""

    def setUp(self) -> None:
        self.done = _activity(
            "J1",
            "1",
            "A",
            "2026-06-01",
            "2026-06-30",
            actual_start=date(2026, 6, 1),
            actual_finish=date(2026, 6, 5),
        )
        self.later = _activity("J2", "1", "B", "2026-06-10", "2026-06-20")
        for att in (self.done, self.later):
            RisorsaAttivita.objects.create(attivita=att, tipo="testa", risorsa_id="H14")

    def test_completed_activity_releases_at_actual_finish(self) -> None:
        self.assertEqual(
            occupation_window(self.done), (date(2026, 6, 1), date(2026, 6, 5))
        )

    def test_no_overlap_after_actual_finish(self) -> None:
        self.assertEqual(find_resource_overlaps([self.done, self.later]), {})

    def test_never_started_still_occupies_its_planned_window(self) -> None:
        stale = _activity("J3", "1", "C", "2026-08-25", "2026-09-01")
        RisorsaAttivita.objects.create(attivita=stale, tipo="testa", risorsa_id="H14")
        blocker = _activity(
            "J4",
            "1",
            "D",
            "2026-08-20",
            "2026-08-30",
            actual_start=date(2026, 8, 20),
        )
        RisorsaAttivita.objects.create(attivita=blocker, tipo="testa", risorsa_id="H14")

        overlaps = find_resource_overlaps([stale, blocker])
        self.assertIn(stale.pk, overlaps)
        self.assertEqual(overlaps[stale.pk][0]["role"], "blocked")
        self.assertEqual(overlaps[blocker.pk][0]["role"], "late")

    def test_two_never_started_activities_also_conflict(self) -> None:
        """The old engine only saw pairs where one had actually started."""
        first = _activity("J5", "1", "E", "2026-10-01", "2026-10-10")
        second = _activity("J6", "1", "F", "2026-10-05", "2026-10-15")
        for att in (first, second):
            RisorsaAttivita.objects.create(attivita=att, tipo="testa", risorsa_id="H99")

        overlaps = find_resource_overlaps([first, second])
        self.assertIn(second.pk, overlaps)
        self.assertEqual(overlaps[second.pk][0]["role"], "blocked")
