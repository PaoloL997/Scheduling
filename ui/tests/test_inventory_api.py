"""Tests for inventory CRUD API endpoints."""

from __future__ import annotations

import json

from django.test import Client, TestCase
from django.urls import reverse

from ui.models import GeneratoreItem
from ui.tests.helpers import create_writer


class InventoryApiTest(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.client.force_login(create_writer("inventarista"))
        GeneratoreItem.objects.create(
            uid="G100",
            model="g1",
            plant="Valbrembo",
            notes="originale",
        )

    def test_add_generatore(self) -> None:
        resp = self.client.post(
            reverse("api_inventario_add", kwargs={"table": "Generatore"}),
            data=json.dumps(
                {
                    "uid": "G200",
                    "model": "g2",
                    "plant": "Plant A",
                    "notes": "nuovo",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["uid"], "G200")
        self.assertTrue(GeneratoreItem.objects.filter(uid="G200").exists())

    def test_add_duplicate_uid_keeps_transaction_usable(self) -> None:
        resp = self.client.post(
            reverse("api_inventario_add", kwargs={"table": "Generatore"}),
            data=json.dumps({"uid": "G100", "model": "g9"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertIn("già presente", resp.json()["error"])
        # PostgreSQL aborts the whole transaction on an unguarded IntegrityError.
        self.assertEqual(GeneratoreItem.objects.get(uid="G100").model, "g1")

    def test_patch_single_field(self) -> None:
        resp = self.client.patch(
            reverse(
                "api_inventario_detail",
                kwargs={"table": "Generatore", "uid": "G100"},
            ),
            data=json.dumps({"plant": "Nuovo stabilimento"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["uid"], "G100")

        item = GeneratoreItem.objects.get(uid="G100")
        self.assertEqual(item.plant, "Nuovo stabilimento")
        self.assertEqual(item.model, "g1")
        self.assertEqual(item.notes, "originale")

    def test_patch_rename_uid(self) -> None:
        resp = self.client.patch(
            reverse(
                "api_inventario_detail",
                kwargs={"table": "Generatore", "uid": "G100"},
            ),
            data=json.dumps({"uid": "G101"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["uid"], "G101")
        self.assertFalse(GeneratoreItem.objects.filter(uid="G100").exists())
        self.assertTrue(GeneratoreItem.objects.filter(uid="G101").exists())

    def test_patch_no_valid_fields(self) -> None:
        resp = self.client.patch(
            reverse(
                "api_inventario_detail",
                kwargs={"table": "Generatore", "uid": "G100"},
            ),
            data=json.dumps({"unknown": "x"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Nessun campo valido", resp.json()["error"])

    def test_patch_not_found(self) -> None:
        resp = self.client.patch(
            reverse(
                "api_inventario_detail",
                kwargs={"table": "Generatore", "uid": "MISSING"},
            ),
            data=json.dumps({"plant": "X"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
