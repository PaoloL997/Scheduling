"""Tests for user registration."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from ui.models import Stabilimento, UserProfile


class RegisterViewTest(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        Stabilimento.objects.create(nome="Valbrembo", colore="#112233")
        Stabilimento.objects.create(nome="Milano", colore="#445566")

    def test_register_creates_user_profile_with_stabilimento(self) -> None:
        resp = self.client.post(
            reverse("register"),
            data={
                "first_name": "Mario",
                "last_name": "Rossi",
                "username": "mario.rossi",
                "email": "mario@esempio.it",
                "stabilimento": "Valbrembo",
                "password1": "SicuraPass123!",
                "password2": "SicuraPass123!",
            },
        )
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(username="mario.rossi")
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.stabilimento.nome, "Valbrembo")

    def test_register_requires_stabilimento_when_configured(self) -> None:
        resp = self.client.post(
            reverse("register"),
            data={
                "first_name": "Luigi",
                "last_name": "Verdi",
                "username": "luigi.verdi",
                "email": "luigi@esempio.it",
                "stabilimento": "",
                "password1": "SicuraPass123!",
                "password2": "SicuraPass123!",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="luigi.verdi").exists())

    def test_register_page_uses_narrow_wrapper(self) -> None:
        resp = self.client.get(reverse("register"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('class="auth-wrapper"', content)
        self.assertIn('id="id_stabilimento"', content)
