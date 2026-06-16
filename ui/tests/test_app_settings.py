"""Tests for AppSettings persistence and impostazioni page."""

from django.contrib.auth.models import User
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse


class ImpostazioniPageTest(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("settingsuser", password="pass")
        self.client.force_login(self.user)

    @override_settings(
        PENALE_MAX="5",
        SOLVER_TIME_LIMIT="10",
        HEARTBEAT_INTERVAL_SECONDS="30",
    )
    def test_impostazioni_shows_default_values(self) -> None:
        resp = self.client.get(reverse("impostazioni"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="input-penale-max"', html)
        self.assertRegex(html, r'id="input-penale-max"[\s\S]*?value="5(\.0)?"')
        self.assertRegex(html, r'id="input-solver-time-limit"[\s\S]*?value="10"')
        self.assertRegex(html, r'id="input-heartbeat-interval"[\s\S]*?value="30"')

    @override_settings(
        PENALE_MAX="7.5",
        SOLVER_TIME_LIMIT="30",
        GEMINI_MODEL="gemini-2.5-pro",
        HEARTBEAT_INTERVAL_SECONDS="45",
    )
    def test_impostazioni_shows_persisted_values(self) -> None:
        resp = self.client.get(reverse("impostazioni"))
        html = resp.content.decode()
        self.assertRegex(html, r'id="input-penale-max"[\s\S]*?value="7\.5"')
        self.assertRegex(html, r'id="input-solver-time-limit"[\s\S]*?value="30"')
        self.assertRegex(html, r'id="input-gemini-model"[\s\S]*?value="gemini-2.5-pro"')
        self.assertRegex(html, r'id="input-heartbeat-interval"[\s\S]*?value="45"')


class AppSettingsApiTest(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("apiuser", password="pass")
        self.client.force_login(self.user)

    @override_settings(
        PENALE_MAX="5",
        SOLVER_TIME_LIMIT="10",
        HEARTBEAT_INTERVAL_SECONDS="30",
        GEMINI_MODEL="gemini-2.5-flash",
    )
    def test_get_returns_defaults(self) -> None:
        resp = self.client.get(reverse("api_app_settings"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["penale_max"], 5.0)
        self.assertEqual(data["solver_time_limit"], 10.0)
        self.assertEqual(data["heartbeat_interval_seconds"], 30)
        self.assertEqual(data["gemini_model"], "gemini-2.5-flash")

    def test_put_is_disabled_for_env_backed_settings(self) -> None:
        resp = self.client.put(
            reverse("api_app_settings"),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["ok"])
        self.assertIn(".env", data["error"])
