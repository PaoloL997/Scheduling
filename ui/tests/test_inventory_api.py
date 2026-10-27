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


class InventoryMeasuresStoredWithDotTest(TestCase):
    """Measures typed with the comma are stored with the dot."""

    def setUp(self) -> None:
        self.client = Client()
        self.client.force_login(create_writer("inventarista"))

    def _post(self, table: str, payload: dict):
        return self.client.post(
            reverse("api_inventario_add", kwargs={"table": table}),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_add_mandrino_with_comma(self) -> None:
        from ui.models import MandrinoItem

        resp = self._post(
            "Mandrino",
            {"uid": "C900", "diameter": "20,5", "plant": "Valbrembo", "notes": "22,5"},
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        item = MandrinoItem.objects.get(uid="C900")
        self.assertEqual(item.diameter, "20.5")
        self.assertEqual(item.notes, "22.5")

    def test_add_maschera_with_comma(self) -> None:
        from ui.models import MascheraItem

        resp = self._post(
            "Maschera",
            {
                "uid": "M900",
                "pitch": "49,21",
                "plant": "Schio",
                "center_distance": "147,6",
            },
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        item = MascheraItem.objects.get(uid="M900")
        self.assertEqual(item.pitch, "49.21")
        self.assertEqual(item.center_distance, "147.6")

    def test_patch_and_put_normalise_too(self) -> None:
        from ui.models import TastatoreItem

        TastatoreItem.objects.create(uid="T900", diameter="13.5", plant="Valbrembo")
        resp = self.client.patch(
            reverse(
                "api_inventario_detail", kwargs={"table": "Tastatore", "uid": "T900"}
            ),
            data=json.dumps({"diameter": "15,08"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(TastatoreItem.objects.get(uid="T900").diameter, "15.08")

        resp = self.client.put(
            reverse(
                "api_inventario_detail", kwargs={"table": "Tastatore", "uid": "T900"}
            ),
            data=json.dumps({"uid": "T900", "diameter": "21,1", "plant": "Valbrembo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(TastatoreItem.objects.get(uid="T900").diameter, "21.1")

    def test_free_text_notes_untouched(self) -> None:
        from ui.models import TestaItem

        resp = self._post(
            "Testa",
            {"uid": "H900", "model": "TP60", "plant": "Valbrembo", "notes": "rev 1,2"},
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(TestaItem.objects.get(uid="H900").notes, "rev 1,2")
