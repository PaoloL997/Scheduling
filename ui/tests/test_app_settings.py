"""Tests for AppSettings persistence and impostazioni page."""

from django.contrib.auth.models import User
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse


class ImpostazioniPageTest(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("settingsuser", password="pass")
        self.client.force_login(self.user)

    def test_impostazioni_shows_only_managed_sections(self) -> None:
        resp = self.client.get(reverse("impostazioni"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertNotIn("Parametri generali", html)
        self.assertNotIn('id="input-penale-max"', html)
        self.assertIn('id="section-stabilimenti"', html)
        self.assertIn('id="section-responsabili"', html)
        self.assertIn('id="section-tipi-attivita"', html)
        self.assertIn('id="section-config-testa"', html)


class AppSettingsApiTest(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("apiuser", password="pass")
        self.client.force_login(self.user)

    @override_settings(
        PENALE_MAX="5",
        SOLVER_TIME_LIMIT="10",
        HEARTBEAT_INTERVAL_SECONDS="30",
        OPENAI_WPS_MODEL="gpt-5.6-sol",
    )
    def test_get_returns_defaults(self) -> None:
        resp = self.client.get(reverse("api_app_settings"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["penale_max"], 5.0)
        self.assertEqual(data["solver_time_limit"], 10.0)
        self.assertEqual(data["heartbeat_interval_seconds"], 30)
        self.assertEqual(data["openai_wps_model"], "gpt-5.6-sol")

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
