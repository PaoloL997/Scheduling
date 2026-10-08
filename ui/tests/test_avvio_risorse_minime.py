"""Tests for the minimum-resource rule that gates starting an activity.

An activity cannot be launched — ``actual_start`` cannot be recorded — until
one testa, one generatore and the per-testa-model minimum of every accessory
are booked.  The rule is the same one behind the ``acquisto`` notes, so an
activity may be started exactly when it carries no open shortfall.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from ui.models import (
    Attivita,
    ConfigurazioneTesta,
    ProblemaAttivita,
    RisorsaAttivita,
)
from ui.services.bridge_write import BridgeWriteError, update_attivita
from ui.services.problem_detection import (
    format_missing_minimum_message,
    missing_minimum_resources,
    resource_shortfalls,
)
from ui.tests.helpers import assign_minimum_kit, create_writer, write_user
from ui.services.planning_read import attivita_to_task_dict
from ui.views import api_tasks_update


def _activity(commessa: str = "C900", **kwargs) -> Attivita:
    """Create a planned, never-started activity."""
    defaults = {
        "item": "1",
        "activity": "produzione",
        "data_inizio": date(2026, 6, 1),
        "data_fine": date(2026, 6, 5),
        "stabilimento": "Valbrembo",
        "modello_testa": "HT200",
    }
    return Attivita.objects.create(commessa=commessa, **{**defaults, **kwargs})


class ResourceShortfallsTest(TestCase):
    """``resource_shortfalls`` is the single definition of the minimum kit."""

    def test_defaults_to_one_per_category(self) -> None:
        shortfalls = resource_shortfalls("SCONOSCIUTO", {})
        self.assertEqual(
            [s["categoria"] for s in shortfalls],
            ["teste", "mandrini", "tastatori", "maschere", "generatori"],
        )
        self.assertTrue(all(s["richiesti"] == 1 for s in shortfalls))

    def test_reads_the_configured_minimums(self) -> None:
        ConfigurazioneTesta.objects.create(
            modello_testa="HT200",
            min_mandrini=2,
            min_tastatori=0,
            min_maschere=0,
            min_generatori=1,
        )
        counts = {"teste": 1, "mandrini": 1, "generatori": 1}
        shortfalls = {s["categoria"]: s for s in resource_shortfalls("HT200", counts)}
        self.assertEqual(set(shortfalls), {"mandrini"})
        self.assertEqual(shortfalls["mandrini"]["selezionati"], 1)
        self.assertEqual(shortfalls["mandrini"]["richiesti"], 2)

    def test_no_shortfall_when_the_kit_is_complete(self) -> None:
        counts = dict.fromkeys(
            ("teste", "mandrini", "tastatori", "maschere", "generatori"), 1
        )
        self.assertEqual(resource_shortfalls("HT200", counts), [])

    def test_missing_minimum_reads_the_booked_resources(self) -> None:
        att = _activity()
        RisorsaAttivita.objects.create(attivita=att, tipo="testa", risorsa_id="T1")
        missing = missing_minimum_resources(att)
        self.assertEqual(
            [s["categoria"] for s in missing],
            ["mandrini", "tastatori", "maschere", "generatori"],
        )

    def test_message_names_every_missing_category(self) -> None:
        att = _activity()
        message = format_missing_minimum_message(missing_minimum_resources(att))
        self.assertIn("Impossibile avviare", message)
        self.assertIn("Testa 0/1", message)
        self.assertIn("Generatore 0/1", message)


class AvvioSenzaRisorseMinimeTest(TestCase):
    """``update_attivita`` refuses the start of an under-equipped activity."""

    def setUp(self) -> None:
        self.att = _activity()

    def test_start_without_any_resource_is_refused(self) -> None:
        with self.assertRaises(BridgeWriteError) as ctx:
            update_attivita(self.att.pk, {"actual_start": "2026-06-01"})

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("risorse minime", ctx.exception.message)
        categorie = [s["categoria"] for s in ctx.exception.missing_resources]
        self.assertEqual(
            categorie, ["teste", "mandrini", "tastatori", "maschere", "generatori"]
        )
        self.att.refresh_from_db()
        self.assertIsNone(self.att.actual_start)

    def test_start_without_generatore_is_refused(self) -> None:
        for tipo, uid in (
            ("testa", "T1"),
            ("mandrino", "M1"),
            ("tastatore", "TA1"),
            ("maschera", "MA1"),
        ):
            RisorsaAttivita.objects.create(attivita=self.att, tipo=tipo, risorsa_id=uid)

        with self.assertRaises(BridgeWriteError) as ctx:
            update_attivita(self.att.pk, {"actual_start": "2026-06-01"})

        self.assertEqual(
            [s["categoria"] for s in ctx.exception.missing_resources], ["generatori"]
        )
        self.assertIn("Generatore 0/1", ctx.exception.message)

    def test_start_below_the_configured_accessory_minimum_is_refused(self) -> None:
        ConfigurazioneTesta.objects.create(modello_testa="HT200", min_mandrini=2)
        assign_minimum_kit(self.att)

        with self.assertRaises(BridgeWriteError) as ctx:
            update_attivita(self.att.pk, {"actual_start": "2026-06-01"})

        self.assertEqual(
            [s["categoria"] for s in ctx.exception.missing_resources], ["mandrini"]
        )
        self.assertIn("Mandrino 1/2", ctx.exception.message)

    def test_start_with_the_minimum_kit_is_accepted(self) -> None:
        assign_minimum_kit(self.att)

        update_attivita(self.att.pk, {"actual_start": "2026-06-01"})

        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, date(2026, 6, 1))

    def test_configured_zero_minimum_only_needs_testa_and_generatore(self) -> None:
        ConfigurazioneTesta.objects.create(
            modello_testa="HT200",
            min_mandrini=0,
            min_tastatori=0,
            min_maschere=0,
            min_generatori=1,
        )
        RisorsaAttivita.objects.create(attivita=self.att, tipo="testa", risorsa_id="T1")
        RisorsaAttivita.objects.create(
            attivita=self.att, tipo="generatore", risorsa_id="G1"
        )

        update_attivita(self.att.pk, {"actual_start": "2026-06-01"})

        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, date(2026, 6, 1))

    def test_resources_sent_with_the_start_are_what_gets_checked(self) -> None:
        """The kit selected in the same request counts, not the stale rows."""
        risorse = {
            "teste": ["T1"],
            "mandrini": ["M1"],
            "tastatori": ["TA1"],
            "maschere": ["MA1"],
            "generatori": ["G1"],
        }
        update_attivita(self.att.pk, {"actual_start": "2026-06-01", "risorse": risorse})

        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, date(2026, 6, 1))
        self.assertEqual(self.att.risorse.count(), 5)

    def test_stripping_the_kit_while_starting_is_refused(self) -> None:
        assign_minimum_kit(self.att)

        with self.assertRaises(BridgeWriteError) as ctx:
            update_attivita(
                self.att.pk,
                {
                    "actual_start": "2026-06-01",
                    "risorse": {
                        "teste": ["T-MIN"],
                        "mandrini": [],
                        "tastatori": [],
                        "maschere": [],
                        "generatori": [],
                    },
                },
            )

        self.assertEqual(ctx.exception.status_code, 409)
        self.att.refresh_from_db()
        self.assertIsNone(self.att.actual_start)
        self.assertEqual(self.att.risorse.count(), 5)

    def test_starting_clears_the_acquisto_note(self) -> None:
        """The blocking rule and the shortfall note share one condition."""
        assign_minimum_kit(self.att)
        update_attivita(
            self.att.pk,
            {
                "actual_start": "2026-06-01",
                "risorse": {
                    "teste": ["T-MIN"],
                    "mandrini": ["M-MIN"],
                    "tastatori": ["TA-MIN"],
                    "maschere": ["MA-MIN"],
                    "generatori": ["G-MIN"],
                },
            },
        )
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.att,
                tipo=ProblemaAttivita.TIPO_ACQUISTO,
                risolto=False,
            ).exists()
        )


class ModificheSuAttivitaGiaAvviataTest(TestCase):
    """Only the transition to «avviata» is guarded, not every later edit."""

    def setUp(self) -> None:
        self.att = _activity(actual_start=date(2026, 6, 1))

    def test_editing_a_started_activity_is_not_refused(self) -> None:
        update_attivita(self.att.pk, {"responsible": "Rossi"})

        self.att.refresh_from_db()
        self.assertEqual(self.att.responsabile, "Rossi")

    def test_closing_a_started_activity_is_not_refused(self) -> None:
        update_attivita(self.att.pk, {"actual_finish": "2026-06-04"})

        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_finish, date(2026, 6, 4))

    def test_echoing_the_same_actual_start_is_not_a_new_start(self) -> None:
        update_attivita(
            self.att.pk,
            {"actual_start": "2026-06-01", "responsible": "Bianchi"},
        )

        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, date(2026, 6, 1))

    def test_restarting_after_a_clear_is_guarded_again(self) -> None:
        update_attivita(self.att.pk, {"actual_start": ""})
        self.att.refresh_from_db()
        self.assertIsNone(self.att.actual_start)

        with self.assertRaises(BridgeWriteError) as ctx:
            update_attivita(self.att.pk, {"actual_start": "2026-06-02"})
        self.assertEqual(ctx.exception.status_code, 409)


class AvvioApiTest(TestCase):
    """Both the web API and the tablet bridge refuse the start with a 409."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = write_user()
        self.att = _activity()

    def _put(self, body: dict):
        request = self.factory.put(
            "/api/tasks/update/",
            data=json.dumps(body),
            content_type="application/json",
        )
        request.user = self.user
        return request

    def test_api_tasks_update_refuses_the_start(self) -> None:
        resp = api_tasks_update(
            self._put({"orm_id": self.att.pk, "actual_start": "2026-06-01"})
        )

        self.assertEqual(resp.status_code, 409)
        payload = json.loads(resp.content)
        self.assertFalse(payload["ok"])
        self.assertIn("risorse minime", payload["error"])
        self.assertEqual(
            [s["categoria"] for s in payload["missing_resources"]],
            ["teste", "mandrini", "tastatori", "maschere", "generatori"],
        )

    def test_api_tasks_update_accepts_an_equipped_activity(self) -> None:
        assign_minimum_kit(self.att)

        resp = api_tasks_update(
            self._put({"orm_id": self.att.pk, "actual_start": "2026-06-01"})
        )

        self.assertEqual(resp.status_code, 200)
        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, date(2026, 6, 1))


class AvvioBridgeApiTest(TestCase):
    def setUp(self) -> None:
        create_writer("tablet-writer", password="pass")
        self.client = Client()
        self.client.login(username="tablet-writer", password="pass")
        self.att = _activity()

    def _put(self):
        return self.client.put(
            reverse("api_bridge_attivita_update", kwargs={"pk": self.att.pk}),
            data=json.dumps({"actual_start": "2026-06-01"}),
            content_type="application/json",
        )

    def test_bridge_refuses_the_start(self) -> None:
        resp = self._put()

        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("risorse minime", body["error"])
        self.assertTrue(body["missing_resources"])
        self.att.refresh_from_db()
        self.assertIsNone(self.att.actual_start)

    def test_bridge_accepts_an_equipped_activity(self) -> None:
        assign_minimum_kit(self.att)

        resp = self._put()

        self.assertEqual(resp.status_code, 200)
        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, date(2026, 6, 1))


class TaskDictStartReadinessTest(TestCase):
    """The task payload tells the clients whether the activity can be started."""

    def test_can_start_is_false_without_the_minimum_kit(self) -> None:
        att = _activity()
        task = attivita_to_task_dict(att)

        self.assertFalse(task["can_start"])
        self.assertEqual(task["min_requirements"]["teste"], 1)
        self.assertEqual(task["min_requirements"]["generatori"], 1)
        self.assertEqual(
            json.loads(task["min_requirements_json"]), task["min_requirements"]
        )

    def test_can_start_is_true_with_the_minimum_kit(self) -> None:
        att = _activity()
        assign_minimum_kit(att)

        task = attivita_to_task_dict(att)

        self.assertTrue(task["can_start"])
        self.assertEqual(task["missing_minimum"], [])

    def test_min_requirements_follow_the_testa_configuration(self) -> None:
        ConfigurazioneTesta.objects.create(modello_testa="HT200", min_mandrini=3)
        att = _activity()

        task = attivita_to_task_dict(att)

        self.assertEqual(task["min_requirements"]["mandrini"], 3)

    def test_row_carries_the_minimums_for_the_client_guard(self) -> None:
        user = create_writer("pian-reader", password="pass")
        client = Client()
        client.force_login(user)
        _activity()

        html = client.get(reverse("pianificazione")).content.decode()

        self.assertIn("data-min-requirements=", html)


class AvvioPanelScriptTest(TestCase):
    """The detail panel blocks the start before the request leaves the page."""

    def test_minimum_kit_guard_in_node(self) -> None:
        if not shutil.which("node"):
            self.skipTest("Node.js not available")
        script = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "avvio_risorse_minime_test.mjs"
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
                "avvio_risorse_minime_test.mjs failed:\n"
                + (proc.stderr or proc.stdout or "unknown error")
            )
        self.assertIn("ok", proc.stdout)
