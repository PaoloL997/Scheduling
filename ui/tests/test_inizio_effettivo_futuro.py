"""Tests for the rule that keeps «inizio effettivo» out of the future.

Recording a start records a fact, and a fact cannot carry the date of a day
that has not come: the operator may only write today or a past date.  Both the
web API and the tablet bridge reach the rule through ``update_attivita``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils.timezone import localdate

from ui.models import Attivita
from ui.services.bridge_write import (
    BridgeWriteError,
    format_future_start_message,
    update_attivita,
    validate_actual_start_not_future,
)
from ui.tests.helpers import assign_minimum_kit, create_writer, write_user
from ui.views import api_tasks_update


def _activity(commessa: str = "C910", **kwargs) -> Attivita:
    """Create a planned activity whose window is already behind us."""
    today = localdate()
    defaults = {
        "item": "1",
        "activity": "produzione",
        "data_inizio": today - timedelta(days=10),
        "data_fine": today - timedelta(days=5),
        "stabilimento": "Valbrembo",
        "modello_testa": "HT200",
    }
    return Attivita.objects.create(commessa=commessa, **{**defaults, **kwargs})


class ValidateActualStartNotFutureTest(TestCase):
    """``validate_actual_start_not_future`` is the single definition."""

    def test_none_is_accepted(self) -> None:
        self.assertIsNone(validate_actual_start_not_future(None))

    def test_today_is_accepted(self) -> None:
        self.assertIsNone(
            validate_actual_start_not_future(date(2026, 6, 10), today=date(2026, 6, 10))
        )

    def test_a_past_date_is_accepted(self) -> None:
        self.assertIsNone(
            validate_actual_start_not_future(date(2026, 5, 1), today=date(2026, 6, 10))
        )

    def test_tomorrow_is_refused(self) -> None:
        with self.assertRaises(BridgeWriteError) as ctx:
            validate_actual_start_not_future(date(2026, 6, 11), today=date(2026, 6, 10))

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("non può essere una data futura", ctx.exception.message)

    def test_the_default_reference_is_the_server_today(self) -> None:
        with self.assertRaises(BridgeWriteError):
            validate_actual_start_not_future(localdate() + timedelta(days=1))
        self.assertIsNone(validate_actual_start_not_future(localdate()))

    def test_the_message_names_both_dates(self) -> None:
        message = format_future_start_message(date(2026, 6, 11), date(2026, 6, 10))

        self.assertIn("2026-06-11", message)
        self.assertIn("2026-06-10", message)
        self.assertIn("data odierna o una data passata", message)


class InizioEffettivoFuturoTest(TestCase):
    """``update_attivita`` refuses a start dated after today."""

    def setUp(self) -> None:
        self.today = localdate()
        self.att = _activity()
        assign_minimum_kit(self.att)

    def test_tomorrow_is_refused(self) -> None:
        domani = self.today + timedelta(days=1)

        with self.assertRaises(BridgeWriteError) as ctx:
            update_attivita(self.att.pk, {"actual_start": domani.isoformat()})

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn(domani.isoformat(), ctx.exception.message)
        self.assertIn(self.today.isoformat(), ctx.exception.message)
        self.att.refresh_from_db()
        self.assertIsNone(self.att.actual_start)

    def test_a_far_future_date_is_refused(self) -> None:
        with self.assertRaises(BridgeWriteError) as ctx:
            update_attivita(
                self.att.pk,
                {"actual_start": (self.today + timedelta(days=90)).isoformat()},
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.att.refresh_from_db()
        self.assertIsNone(self.att.actual_start)

    def test_today_is_accepted(self) -> None:
        update_attivita(self.att.pk, {"actual_start": self.today.isoformat()})

        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, self.today)

    def test_a_past_date_is_accepted(self) -> None:
        ieri = self.today - timedelta(days=1)

        update_attivita(self.att.pk, {"actual_start": ieri.isoformat()})

        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, ieri)

    def test_nothing_else_in_the_request_is_written(self) -> None:
        """The refusal happens before the save: no partial update survives."""
        with self.assertRaises(BridgeWriteError):
            update_attivita(
                self.att.pk,
                {
                    "actual_start": (self.today + timedelta(days=2)).isoformat(),
                    "responsible": "Rossi",
                },
            )

        self.att.refresh_from_db()
        self.assertEqual(self.att.responsabile, "")
        self.assertIsNone(self.att.actual_start)

    def test_clearing_the_field_is_not_a_future_date(self) -> None:
        update_attivita(self.att.pk, {"actual_start": self.today.isoformat()})

        update_attivita(self.att.pk, {"actual_start": ""})

        self.att.refresh_from_db()
        self.assertIsNone(self.att.actual_start)

    def test_a_future_planned_start_is_still_allowed(self) -> None:
        """Only the *effective* start is a fact; planning ahead is the point."""
        domani = self.today + timedelta(days=1)

        update_attivita(
            self.att.pk,
            {
                "start": domani.isoformat(),
                "finish": (domani + timedelta(days=3)).isoformat(),
            },
        )

        self.att.refresh_from_db()
        self.assertEqual(self.att.data_inizio, domani)

    def test_a_future_actual_finish_is_out_of_scope(self) -> None:
        """The rule guards the start; closing dates keep their own checks."""
        update_attivita(
            self.att.pk,
            {
                "actual_start": self.today.isoformat(),
                "actual_finish": (self.today + timedelta(days=1)).isoformat(),
            },
        )

        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, self.today)


class InizioEffettivoGiaRegistratoTest(TestCase):
    """Only a *new* value is checked, so stale data stays editable."""

    def setUp(self) -> None:
        self.today = localdate()
        self.futuro = self.today + timedelta(days=7)
        # A value the .mpp import could carry in from before the rule existed.
        self.att = _activity(actual_start=self.futuro)

    def test_echoing_the_stored_future_start_is_not_refused(self) -> None:
        update_attivita(
            self.att.pk,
            {"actual_start": self.futuro.isoformat(), "responsible": "Bianchi"},
        )

        self.att.refresh_from_db()
        self.assertEqual(self.att.responsabile, "Bianchi")
        self.assertEqual(self.att.actual_start, self.futuro)

    def test_an_unrelated_edit_is_not_refused(self) -> None:
        update_attivita(self.att.pk, {"responsible": "Verdi"})

        self.att.refresh_from_db()
        self.assertEqual(self.att.responsabile, "Verdi")

    def test_correcting_it_to_today_is_accepted(self) -> None:
        update_attivita(self.att.pk, {"actual_start": self.today.isoformat()})

        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, self.today)

    def test_moving_it_to_another_future_day_is_refused(self) -> None:
        with self.assertRaises(BridgeWriteError) as ctx:
            update_attivita(
                self.att.pk,
                {"actual_start": (self.futuro + timedelta(days=1)).isoformat()},
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, self.futuro)


class InizioEffettivoFuturoApiTest(TestCase):
    """The web API answers 400 and leaves the activity untouched."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = write_user()
        self.today = localdate()
        self.att = _activity()
        assign_minimum_kit(self.att)

    def _put(self, body: dict):
        request = self.factory.put(
            "/api/tasks/update/",
            data=json.dumps(body),
            content_type="application/json",
        )
        request.user = self.user
        return request

    def test_api_tasks_update_refuses_a_future_start(self) -> None:
        domani = self.today + timedelta(days=1)

        resp = api_tasks_update(
            self._put({"orm_id": self.att.pk, "actual_start": domani.isoformat()})
        )

        self.assertEqual(resp.status_code, 400)
        payload = json.loads(resp.content)
        self.assertFalse(payload["ok"])
        self.assertIn("non può essere una data futura", payload["error"])
        self.att.refresh_from_db()
        self.assertIsNone(self.att.actual_start)

    def test_api_tasks_update_accepts_today(self) -> None:
        resp = api_tasks_update(
            self._put({"orm_id": self.att.pk, "actual_start": self.today.isoformat()})
        )

        self.assertEqual(resp.status_code, 200)
        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, self.today)


class InizioEffettivoFuturoBridgeTest(TestCase):
    """The tablet bridge is held to the same rule."""

    def setUp(self) -> None:
        create_writer("tablet-futuro", password="pass")
        self.client = Client()
        self.client.login(username="tablet-futuro", password="pass")
        self.today = localdate()
        self.att = _activity()
        assign_minimum_kit(self.att)

    def _put(self, actual_start: str):
        return self.client.put(
            reverse("api_bridge_attivita_update", kwargs={"pk": self.att.pk}),
            data=json.dumps({"actual_start": actual_start}),
            content_type="application/json",
        )

    def test_bridge_refuses_a_future_start(self) -> None:
        resp = self._put((self.today + timedelta(days=1)).isoformat())

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("non può essere una data futura", body["error"])
        self.att.refresh_from_db()
        self.assertIsNone(self.att.actual_start)

    def test_bridge_accepts_today(self) -> None:
        resp = self._put(self.today.isoformat())

        self.assertEqual(resp.status_code, 200)
        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, self.today)


class InizioEffettivoPanelScriptTest(TestCase):
    """The detail panel stops a future start before the request leaves."""

    def test_future_start_guard_in_node(self) -> None:
        if not shutil.which("node"):
            self.skipTest("Node.js not available")
        script = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "inizio_effettivo_futuro_test.mjs"
        )
        if not script.is_file():
            self.fail(f"Missing panel test script: {script}")
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
                "inizio_effettivo_futuro_test.mjs failed:\n"
                + (proc.stderr or proc.stdout or "unknown error")
            )
        self.assertIn("ok", proc.stdout)
