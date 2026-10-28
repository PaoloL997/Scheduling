"""Tests for starting an activity whose pieces another activity holds.

A piece booked by a *planned* activity in the window is physically free: the
start is recorded with a warning and the planned holder is re-equipped or
offered a reschedule.  A piece held by an activity *in progress* is really in
use: the start is refused.  A closed holder has released its pieces.
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils.timezone import localdate

from ui.models import Attivita, ProblemaAttivita, RisorsaAttivita
from ui.services.bridge_write import BridgeWriteError, update_attivita
from ui.tests.helpers import assign_minimum_kit, create_writer


def _activity(commessa: str, start, finish, **kwargs) -> Attivita:
    return Attivita.objects.create(
        commessa=commessa,
        item="1",
        activity="produzione",
        data_inizio=start,
        data_fine=finish,
        stabilimento="Valbrembo",
        modello_testa="HT200",
        **kwargs,
    )


class AvvioBase(TestCase):
    def setUp(self) -> None:
        self.today = localdate()
        # Planned 18 days ahead and started early today, like 25100.
        self.att = _activity(
            "C100", self.today + timedelta(days=18), self.today + timedelta(days=33)
        )
        kit = assign_minimum_kit(self.att)
        self.testa = next(r.risorsa_id for r in kit if r.tipo == "testa")

    def _holder(
        self, start_days: int, finish_days: int, commessa: str = "C200", **kwargs
    ) -> Attivita:
        """Another activity holding the same testa as ``self.att``."""
        other = _activity(
            commessa,
            self.today + timedelta(days=start_days),
            self.today + timedelta(days=finish_days),
            **kwargs,
        )
        assign_minimum_kit(other, suffix="-B")
        RisorsaAttivita.objects.create(
            attivita=other, tipo="testa", risorsa_id=self.testa
        )
        return other

    def _start(self, **extra) -> dict:
        return update_attivita(
            self.att.pk, {"actual_start": self.today.isoformat(), **extra}
        )


class AvvioConPezziPianificatiTest(AvvioBase):
    """A planned holder yields a warning, never a refusal."""

    def test_start_is_recorded_with_a_warning(self) -> None:
        other = self._holder(-1, 16)

        result = self._start()

        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, self.today)
        self.assertEqual(result["warnings"][0]["attivita_id"], other.pk)
        self.assertEqual(result["warnings"][0]["risorsa_id"], self.testa)
        self.assertIn(self.testa, result["warning_message"])
        self.assertIn(str(other), result["warning_message"])
        self.assertIn("destinati a un'altra attività", result["warning_message"])

    def test_planned_holder_is_flagged_or_reequipped(self) -> None:
        other = self._holder(-1, 16)

        self._start()

        still_holds = RisorsaAttivita.objects.filter(
            attivita=other, tipo="testa", risorsa_id=self.testa
        ).exists()
        flagged = ProblemaAttivita.objects.filter(
            attivita=other, risolto=False
        ).exists()
        self.assertTrue(flagged or not still_holds)
        self.assertTrue(
            RisorsaAttivita.objects.filter(
                attivita=self.att, tipo="testa", risorsa_id=self.testa
            ).exists()
        )

    def test_holder_outside_the_window_gives_no_warning(self) -> None:
        self._holder(40, 50)

        result = self._start()

        self.assertNotIn("warnings", result)

    def test_closed_holder_gives_only_a_warning(self) -> None:
        self._holder(
            -10,
            -2,
            actual_start=self.today - timedelta(days=10),
            actual_finish=self.today,
        )

        result = self._start()

        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, self.today)
        self.assertIn("warnings", result)


class AvvioConPezziInUsoTest(AvvioBase):
    """A holder in progress refuses the start."""

    def test_in_progress_holder_refuses(self) -> None:
        other = self._holder(-10, -2, actual_start=self.today - timedelta(days=10))

        with self.assertRaises(BridgeWriteError) as ctx:
            self._start()

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.conflicts[0]["attivita_id"], other.pk)
        self.assertIn(self.testa, ctx.exception.message)
        self.assertIn("in uso in un'attività in corso", ctx.exception.message)
        self.att.refresh_from_db()
        self.assertIsNone(self.att.actual_start)

    def test_only_the_in_progress_holder_is_reported(self) -> None:
        planned = self._holder(-1, 16)
        running = self._holder(
            -5, 5, commessa="C300", actual_start=self.today - timedelta(days=5)
        )

        with self.assertRaises(BridgeWriteError) as ctx:
            self._start()

        reported = {c["attivita_id"] for c in ctx.exception.conflicts}
        self.assertEqual(reported, {running.pk})
        self.assertNotIn(planned.pk, reported)

    def test_editing_an_activity_already_started_is_not_refused(self) -> None:
        Attivita.objects.filter(pk=self.att.pk).update(actual_start=self.today)
        self._holder(-5, 5, actual_start=self.today - timedelta(days=5))

        result = update_attivita(self.att.pk, {"responsible": "Bianchi"})

        self.att.refresh_from_db()
        self.assertEqual(self.att.responsabile, "Bianchi")
        self.assertIn("warnings", result)

    def test_closing_an_activity_already_started_is_not_refused(self) -> None:
        Attivita.objects.filter(pk=self.att.pk).update(actual_start=self.today)
        self._holder(-5, 5, actual_start=self.today - timedelta(days=5))

        update_attivita(self.att.pk, {"actual_finish": self.today.isoformat()})

        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_finish, self.today)


class AvvioBridgeTest(AvvioBase):
    """The tablet gets the same answers through the bridge."""

    def setUp(self) -> None:
        super().setUp()
        create_writer("tablet-writer", password="pass")
        self.client = Client()
        self.client.login(username="tablet-writer", password="pass")

    def _put(self):
        return self.client.put(
            reverse("api_bridge_attivita_update", kwargs={"pk": self.att.pk}),
            data=json.dumps({"actual_start": self.today.isoformat()}),
            content_type="application/json",
        )

    def test_planned_holder_starts_with_warning_message(self) -> None:
        other = self._holder(-1, 16)

        resp = self._put()

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertIn(str(other), body["warning_message"])
        self.assertEqual(body["warnings"][0]["attivita_id"], other.pk)

    def test_in_progress_holder_is_refused_with_conflicts(self) -> None:
        other = self._holder(-5, 5, actual_start=self.today - timedelta(days=5))

        resp = self._put()

        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn(self.testa, body["error"])
        self.assertEqual(body["conflicts"][0]["attivita_id"], other.pk)
        self.att.refresh_from_db()
        self.assertIsNone(self.att.actual_start)
