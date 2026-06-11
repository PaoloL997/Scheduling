"""Tests for AppSettings persistence and impostazioni page."""

import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from ui.models import AppSettings


class AppSettingsLoadTest(TestCase):
    def test_load_creates_defaults_on_empty_db(self) -> None:
        AppSettings.objects.all().delete()
        settings = AppSettings.load()
        self.assertEqual(float(settings.penale_max), 5.0)
        self.assertEqual(settings.solver_time_limit, 10.0)


class ImpostazioniPageTest(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("settingsuser", password="pass")
        self.client.force_login(self.user)

    def test_impostazioni_shows_default_values(self) -> None:
        AppSettings.objects.all().delete()
        AppSettings.load()

        resp = self.client.get(reverse("impostazioni"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="input-penale-max"', html)
        self.assertRegex(html, r'id="input-penale-max"[\s\S]*?value="5(\.0)?"')
        self.assertRegex(html, r'id="input-solver-time-limit"[\s\S]*?value="10"')

    def test_impostazioni_shows_persisted_values(self) -> None:
        settings = AppSettings.load()
        settings.penale_max = 7.5
        settings.solver_time_limit = 30
        settings.save()

        resp = self.client.get(reverse("impostazioni"))
        html = resp.content.decode()
        self.assertRegex(html, r'id="input-penale-max"[\s\S]*?value="7\.5"')
        self.assertRegex(html, r'id="input-solver-time-limit"[\s\S]*?value="30"')


class AppSettingsApiTest(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("apiuser", password="pass")
        self.client.force_login(self.user)
        AppSettings.objects.all().delete()
        AppSettings.load()

    def test_get_returns_defaults(self) -> None:
        resp = self.client.get(reverse("api_app_settings"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["penale_max"], 5.0)
        self.assertEqual(data["solver_time_limit"], 10.0)

    def test_put_persists_values(self) -> None:
        resp = self.client.put(
            reverse("api_app_settings"),
            data=json.dumps({"penale_max": 12, "solver_time_limit": 45}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["penale_max"], 12.0)
        self.assertEqual(data["solver_time_limit"], 45.0)

        settings = AppSettings.load()
        self.assertEqual(float(settings.penale_max), 12.0)
        self.assertEqual(settings.solver_time_limit, 45.0)

        get_resp = self.client.get(reverse("api_app_settings"))
        get_data = get_resp.json()
        self.assertEqual(get_data["penale_max"], 12.0)
        self.assertEqual(get_data["solver_time_limit"], 45.0)
