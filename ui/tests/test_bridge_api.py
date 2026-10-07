"""Tests for the tablet bridge API."""

from __future__ import annotations

import json
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from ui.models import (
    Attivita,
    Job,
    MandrinoItem,
    RisorsaAttivita,
    Stabilimento,
    TestaItem,
    UserProfile,
)
from ui.tests.helpers import assign_minimum_kit, create_writer


class BridgeAuthTest(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        User.objects.create_user("tablet", password="secret")

    def test_login_success(self) -> None:
        resp = self.client.post(
            reverse("api_bridge_auth_login"),
            data=json.dumps({"username": "tablet", "password": "secret"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["username"], "tablet")

    def test_login_invalid_credentials(self) -> None:
        resp = self.client.post(
            reverse("api_bridge_auth_login"),
            data=json.dumps({"username": "tablet", "password": "wrong"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_me_requires_auth(self) -> None:
        resp = self.client.get(reverse("api_bridge_auth_me"))
        self.assertEqual(resp.status_code, 302)

    def test_me_after_login(self) -> None:
        self.client.login(username="tablet", password="secret")
        resp = self.client.get(reverse("api_bridge_auth_me"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["username"], "tablet")


class BridgeReadTest(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("reader", password="pass")
        self.client = Client()
        self.client.login(username="reader", password="pass")
        self.job = Job.objects.create(commessa="B100", delivery_date="2026-12-01")
        self.att = Attivita.objects.create(
            commessa="B100",
            item=1,
            activity="Montaggio",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
        )
        Stabilimento.objects.create(nome="Plant A", colore="#ff0000")

    def test_attivita_list(self) -> None:
        resp = self.client.get(reverse("api_bridge_attivita_list"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["commessa"], "B100")
        self.assertIn("status_pills", data[0])

    def test_attivita_detail(self) -> None:
        resp = self.client.get(
            reverse("api_bridge_attivita_detail", kwargs={"pk": self.att.pk})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["orm_id"], self.att.pk)

    def test_jobs(self) -> None:
        resp = self.client.get(reverse("api_bridge_jobs"))
        self.assertEqual(resp.status_code, 200)
        jobs = resp.json()["data"]
        self.assertEqual(jobs[0]["commessa"], "B100")

    def test_dataset(self) -> None:
        resp = self.client.get(reverse("api_bridge_dataset"))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["data"]
        self.assertIn("attivita", payload)
        self.assertIn("inventario", payload)
        self.assertIn("stabilimenti", payload)

    def test_dataset_filters_attivita_by_user_plant(self) -> None:
        valbrembo = Stabilimento.objects.create(nome="Valbrembo", colore="#00aa00")
        UserProfile.objects.update_or_create(
            user=self.user, defaults={"stabilimento": valbrembo}
        )
        local = Attivita.objects.create(
            commessa="B200",
            item=1,
            activity="Locale",
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
        )
        Attivita.objects.create(
            commessa="B300",
            item=1,
            activity="Altro",
            stabilimento="Schio",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
        )

        resp = self.client.get(reverse("api_bridge_dataset"))
        self.assertEqual(resp.status_code, 200)
        attivita = resp.json()["data"]["attivita"]
        self.assertEqual(len(attivita), 1)
        self.assertEqual(attivita[0]["plant"], "Valbrembo")
        self.assertEqual(attivita[0]["orm_id"], local.pk)

        list_resp = self.client.get(reverse("api_bridge_attivita_list"))
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(len(list_resp.json()["data"]), 1)

    def test_dataset_plant_filter_is_case_insensitive(self) -> None:
        valbrembo = Stabilimento.objects.create(nome="Valbrembo", colore="#00aa00")
        UserProfile.objects.update_or_create(
            user=self.user, defaults={"stabilimento": valbrembo}
        )
        Attivita.objects.create(
            commessa="B210",
            item=1,
            activity="Lowercase",
            stabilimento="valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
        )

        resp = self.client.get(reverse("api_bridge_attivita_list"))
        plants = {row["plant"] for row in resp.json()["data"]}
        self.assertEqual(plants, {"valbrembo"})

    def test_dataset_without_profile_returns_all_activities(self) -> None:
        Attivita.objects.create(
            commessa="B220",
            item=1,
            activity="Altro",
            stabilimento="Schio",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
        )
        resp = self.client.get(reverse("api_bridge_attivita_list"))
        self.assertEqual(len(resp.json()["data"]), 2)

    def test_dataset_keeps_full_inventario_when_filtering_attivita(self) -> None:
        valbrembo = Stabilimento.objects.create(nome="Valbrembo", colore="#00aa00")
        UserProfile.objects.update_or_create(
            user=self.user, defaults={"stabilimento": valbrembo}
        )
        TestaItem.objects.create(uid="OV-T-SCHIO", model="TP60", plant="Schio")

        resp = self.client.get(reverse("api_bridge_dataset"))
        inventario = resp.json()["data"]["inventario"]
        uids = {row["uid"] for row in inventario.get("Testa", [])}
        self.assertIn("OV-T-SCHIO", uids)

    def test_dataset_inventario_carries_availability_from_every_plant(self) -> None:
        # The tablet only receives its own plant's activities: availability must
        # come from the server, over every plant, with the inventory page's rule.
        valbrembo = Stabilimento.objects.create(nome="Valbrembo", colore="#00aa00")
        UserProfile.objects.update_or_create(
            user=self.user, defaults={"stabilimento": valbrembo}
        )
        today = date.today()
        elsewhere = Attivita.objects.create(
            commessa="B400",
            item=1,
            activity="Altrove",
            stabilimento="Schio",
            data_inizio=today - timedelta(days=1),
            data_fine=today + timedelta(days=5),
        )
        # Planned only, never started: it still holds its resources today.
        RisorsaAttivita.objects.create(
            attivita=elsewhere, tipo="testa", risorsa_id="AV-T-BUSY"
        )
        RisorsaAttivita.objects.create(
            attivita=elsewhere, tipo="mandrino", risorsa_id="AV-M-BUSY"
        )
        TestaItem.objects.create(uid="AV-T-BUSY", model="TP60", plant="Valbrembo")
        TestaItem.objects.create(uid="AV-T-FREE", model="TP60", plant="Valbrembo")
        MandrinoItem.objects.create(uid="AV-M-BUSY", diameter="12", plant="Valbrembo")

        resp = self.client.get(reverse("api_bridge_dataset"))
        data = resp.json()["data"]

        self.assertNotIn("Schio", {a["plant"] for a in data["attivita"]})

        teste = {row["uid"]: row for row in data["inventario"]["Testa"]}
        busy = teste["AV-T-BUSY"]["disponibilita"]
        self.assertFalse(busy["libera"])
        self.assertEqual(busy["stato"], "occupata")
        self.assertTrue(busy["tooltip"].startswith("Occupata oggi"))
        self.assertTrue(teste["AV-T-FREE"]["disponibilita"]["libera"])
        self.assertNotIn("disponibilita_html", teste["AV-T-FREE"])

        mandrini = {row["uid"]: row for row in data["inventario"]["Mandrino"]}
        self.assertFalse(mandrini["AV-M-BUSY"]["disponibilita"]["libera"])


class BridgeInventoryWriteTest(TestCase):
    def setUp(self) -> None:
        self.user = create_writer("tablet-BR1", password="pass")
        self.client = Client()
        self.client.login(username="tablet-BR1", password="pass")
        TestaItem.objects.create(uid="OV-T1", model="TP60", plant="Schio")

    def test_create_component_at_user_plant(self) -> None:
        resp = self.client.post(
            reverse("api_bridge_inventario_create", kwargs={"table": "Testa"}),
            data=json.dumps({"uid": "OV-T9", "model": "TP60", "plant": "Valbrembo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        item = TestaItem.objects.get(uid="OV-T9")
        self.assertEqual(item.plant, "Valbrembo")
        self.assertEqual(item.model, "TP60")

    def test_move_component_to_user_plant(self) -> None:
        resp = self.client.patch(
            reverse(
                "api_bridge_inventario_detail",
                kwargs={"table": "Testa", "uid": "OV-T1"},
            ),
            data=json.dumps({"plant": "Valbrembo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        item = TestaItem.objects.get(uid="OV-T1")
        self.assertEqual(item.plant, "Valbrembo")
        self.assertEqual(item.model, "TP60")

    def test_toggle_maintenance_on_testa(self) -> None:
        resp = self.client.patch(
            reverse(
                "api_bridge_inventario_detail",
                kwargs={"table": "Testa", "uid": "OV-T1"},
            ),
            data=json.dumps({"in_manutenzione": True}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        item = TestaItem.objects.get(uid="OV-T1")
        self.assertTrue(item.in_manutenzione)

        resp = self.client.patch(
            reverse(
                "api_bridge_inventario_detail",
                kwargs={"table": "Testa", "uid": "OV-T1"},
            ),
            data=json.dumps({"in_manutenzione": False}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        item = TestaItem.objects.get(uid="OV-T1")
        self.assertFalse(item.in_manutenzione)

    def test_create_does_not_overwrite_existing_uid(self) -> None:
        resp = self.client.post(
            reverse("api_bridge_inventario_create", kwargs={"table": "Testa"}),
            data=json.dumps({"uid": "OV-T1", "model": "TP70", "plant": "Valbrembo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertFalse(resp.json().get("ok", True))


class BridgeWriteTest(TestCase):
    def setUp(self) -> None:
        self.user = create_writer("writer", password="pass")
        self.client = Client()
        self.client.login(username="writer", password="pass")
        self.att = Attivita.objects.create(
            commessa="W100",
            item=1,
            activity="Test",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
        )

    def test_update_attivita(self) -> None:
        # Recording the start requires the minimum resource kit.
        assign_minimum_kit(self.att)
        resp = self.client.put(
            reverse(
                "api_bridge_attivita_update",
                kwargs={"pk": self.att.pk},
            ),
            data=json.dumps({"actual_start": "2026-06-02"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.att.refresh_from_db()
        self.assertEqual(self.att.actual_start, date(2026, 6, 2))

    def test_clear_actual_finish(self) -> None:
        self.att.actual_finish = date(2026, 6, 4)
        self.att.save(update_fields=["actual_finish"])
        resp = self.client.put(
            reverse(
                "api_bridge_attivita_update",
                kwargs={"pk": self.att.pk},
            ),
            data=json.dumps({"actual_finish": ""}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.att.refresh_from_db()
        self.assertIsNone(self.att.actual_finish)

    def test_delete_attivita(self) -> None:
        resp = self.client.delete(
            reverse(
                "api_bridge_attivita_delete",
                kwargs={"pk": self.att.pk},
            )
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Attivita.objects.filter(pk=self.att.pk).exists())

    def test_create_job(self) -> None:
        resp = self.client.post(
            reverse("api_bridge_jobs_create"),
            data=json.dumps(
                {
                    "commessa": "NEW01",
                    "delivery_date": "2026-12-31",
                    "total_cost": 1000,
                    "penalty": 1.0,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(Job.objects.filter(commessa="NEW01").exists())


class BridgeStatusTest(TestCase):
    """The dataset lives in the database: status is always ready."""

    def setUp(self) -> None:
        self.user = create_writer("importer", password="pass")
        self.client = Client()
        self.client.login(username="importer", password="pass")

    def test_status_keeps_the_legacy_shape(self) -> None:
        resp = self.client.get(reverse("api_bridge_status"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "ok": True,
                "data": {
                    "status": "ready",
                    "detail": "",
                    "pending_queue_count": 0,
                    "sync_phase": "ready",
                },
            },
        )

    def test_import_is_gone(self) -> None:
        resp = self.client.post(
            reverse("api_bridge_import"),
            data=json.dumps({"force": True}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 410)
        self.assertFalse(resp.json()["accepted"])

    def test_import_requires_auth(self) -> None:
        self.client.logout()
        resp = self.client.post(
            reverse("api_bridge_import"), data="{}", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 302)
