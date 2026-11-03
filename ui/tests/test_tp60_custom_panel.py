"""The TP60 Custom flag can be changed from the pianificazione detail panel.

The panel reads the flag from the row (``data-tp60-custom``), sends it to
``/api/tasks/update/`` only when it changes, and refreshes the row's minimums
from the ``task`` the API returns.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from ui.models import Attivita
from ui.tests.helpers import create_writer
from ui.views import api_tasks_update


def _activity(commessa: str, **kwargs) -> Attivita:
    defaults = {
        "item": "1",
        "activity": "produzione",
        "data_inizio": date(2026, 6, 1),
        "data_fine": date(2026, 6, 5),
        "stabilimento": "Valbrembo",
        "modello_testa": "TP60",
    }
    return Attivita.objects.create(commessa=commessa, **{**defaults, **kwargs})


class Tp60CustomRowTest(TestCase):
    """Each planning row carries the flag for the panel."""

    def test_row_carries_the_flag(self) -> None:
        user = create_writer("pian-tp60", password="pass")
        client = Client()
        client.force_login(user)
        flagged = _activity("C801", tp60_custom=True)
        plain = _activity("C802")

        html = client.get(reverse("pianificazione")).content.decode()

        self.assertRegex(html, rf'data-orm-id="{flagged.pk}"[^>]*data-tp60-custom="1"')
        self.assertRegex(html, rf'data-orm-id="{plain.pk}"[^>]*data-tp60-custom=""')


class Tp60CustomApiTest(TestCase):
    """The update API returns the minimums that follow the new flag."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = create_writer("web-tp60", password="pass")

    def _put(self, payload: dict) -> dict:
        request = self.factory.put(
            "/api/tasks/update/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        request.user = self.user
        resp = api_tasks_update(request)
        self.assertEqual(resp.status_code, 200, resp.content)
        return json.loads(resp.content)

    def test_ticking_the_flag_returns_the_tp60_minimums(self) -> None:
        att = _activity("C803")

        data = self._put({"orm_id": att.pk, "tp60_custom": True})

        att.refresh_from_db()
        self.assertTrue(att.tp60_custom)
        self.assertTrue(data["task"]["tp60_custom"])
        self.assertEqual(data["task"]["min_requirements"]["mandrini"], 2)
        self.assertEqual(data["task"]["min_requirements"]["tastatori"], 0)

    def test_unticking_the_flag_restores_the_configured_minimums(self) -> None:
        att = _activity("C804", tp60_custom=True)

        data = self._put({"orm_id": att.pk, "tp60_custom": False})

        att.refresh_from_db()
        self.assertFalse(att.tp60_custom)
        self.assertEqual(data["task"]["min_requirements"]["tastatori"], 1)

    def test_a_save_without_the_key_keeps_the_flag(self) -> None:
        att = _activity("C805", tp60_custom=True)

        self._put({"orm_id": att.pk, "responsible": "Rossi"})

        att.refresh_from_db()
        self.assertTrue(att.tp60_custom)


class Tp60CustomPanelScriptTest(TestCase):
    """The detail panel edits the flag (jsdom)."""

    def test_panel_in_node(self) -> None:
        if not shutil.which("node"):
            self.skipTest("Node.js not available")
        script = (
            Path(__file__).resolve().parent / "fixtures" / "tp60_custom_panel_test.mjs"
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
                "tp60_custom_panel_test.mjs failed:\n"
                + (proc.stderr or proc.stdout or "unknown error")
            )
        self.assertIn("ok", proc.stdout)
