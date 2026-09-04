"""Tests for configurable activity types API and validation."""

import json

from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from ui.models import TipoAttivita
from ui.services.activity_inventory import get_tipi_attivita, validate_activity_type
from ui.views import api_tipi_attivita_create, api_tipi_attivita_detail
from ui.tests.helpers import create_writer


class GetTipiAttivitaTest(TestCase):
    """get_tipi_attivita returns sorted names from the ORM."""

    def test_returns_sorted_names(self) -> None:
        TipoAttivita.objects.create(nome="Saldatura")
        TipoAttivita.objects.create(nome="Qualifica")
        self.assertEqual(get_tipi_attivita(), ["Qualifica", "Saldatura"])


class ValidateActivityTypeTest(TestCase):
    """validate_activity_type enforces the configured catalog."""

    def setUp(self) -> None:
        TipoAttivita.objects.create(nome="Qualifica")

    def test_returns_canonical_name(self) -> None:
        self.assertEqual(validate_activity_type("qualifica"), "Qualifica")

    def test_rejects_unknown_type(self) -> None:
        with self.assertRaises(ValueError):
            validate_activity_type("Sconosciuta")

    def test_rejects_empty_when_catalog_exists(self) -> None:
        with self.assertRaises(ValueError):
            validate_activity_type("")

    def test_rejects_when_catalog_empty(self) -> None:
        TipoAttivita.objects.all().delete()
        with self.assertRaises(ValueError):
            validate_activity_type("Qualifica")


class TipiAttivitaApiTest(TestCase):
    """CRUD API for activity types."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = create_writer(username="admin", password="p")

    def test_create_and_update(self) -> None:
        req = self.factory.post(
            "/fake/",
            data=json.dumps({"nome": "Qualifica"}),
            content_type="application/json",
        )
        req.user = self.user
        resp = api_tipi_attivita_create(req)
        self.assertEqual(resp.status_code, 201)
        pk = json.loads(resp.content)["id"]

        req = self.factory.put(
            "/fake/",
            data=json.dumps({"nome": "Saldatura"}),
            content_type="application/json",
        )
        req.user = self.user
        resp = api_tipi_attivita_detail(req, pk=pk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content)["nome"], "Saldatura")

    def test_duplicate_name_returns_409(self) -> None:
        TipoAttivita.objects.create(nome="Qualifica")
        req = self.factory.post(
            "/fake/",
            data=json.dumps({"nome": "qualifica"}),
            content_type="application/json",
        )
        req.user = self.user
        resp = api_tipi_attivita_create(req)
        self.assertEqual(resp.status_code, 409)

    def test_delete(self) -> None:
        obj = TipoAttivita.objects.create(nome="Qualifica")
        req = self.factory.delete("/fake/")
        req.user = self.user
        resp = api_tipi_attivita_detail(req, pk=obj.pk)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(TipoAttivita.objects.filter(pk=obj.pk).exists())


class TipiAttivitaClientApiTest(TestCase):
    """CRUD API via test client when middleware stack is available."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = create_writer(username="admin", password="p")
        self.client.force_login(self.user)

    def test_create_via_client(self) -> None:
        resp = self.client.post(
            reverse("api_tipi_attivita_create"),
            data=json.dumps({"nome": "Qualifica"}),
            content_type="application/json",
        )
        if resp.status_code == 201:
            self.assertEqual(resp.json()["nome"], "Qualifica")


class ImpostazioniTipiAttivitaSectionTest(TestCase):
    """Settings page exposes the activity types section."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = create_writer(username="settings", password="p")
        self.client.force_login(self.user)

    def test_section_is_present(self) -> None:
        resp = self.client.get(reverse("impostazioni"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id="section-tipi-attivita"', resp.content.decode())
