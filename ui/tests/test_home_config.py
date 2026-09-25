"""Tests for the configuration the home page hands to its script.

The plant filter starts on the user's own stabilimento.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from ui.models import Stabilimento, UserProfile
from ui.tests.helpers import create_reader


class HomeConfigTest(TestCase):
    """``home_config`` in the index context."""

    def setUp(self) -> None:
        self.ricengo = Stabilimento.objects.create(nome="Ricengo", colore="#2926ed")
        self.user = create_reader()
        self.client.force_login(self.user)

    def _config(self) -> dict:
        return self.client.get(reverse("index")).context["home_config"]

    def test_user_plant_preselects_the_filter(self) -> None:
        UserProfile.objects.filter(user=self.user).update(stabilimento=self.ricengo)
        self.assertEqual(self._config()["userPlant"], "Ricengo")

    def test_no_plant_means_no_default_filter(self) -> None:
        self.assertEqual(self._config()["userPlant"], "")

    def test_config_is_embedded_as_json(self) -> None:
        html = self.client.get(reverse("index")).content.decode()
        self.assertIn('id="home-config"', html)
