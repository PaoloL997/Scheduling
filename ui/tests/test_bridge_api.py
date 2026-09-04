"""Tests for the tablet bridge API."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from ui.models import Attivita, Job, MppSyncQueue, Stabilimento, TestaItem, UserProfile
from ui.services.importer import ImportStatus
from ui.tests.helpers import create_writer


def _ready_status():
    return ImportStatus.READY


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


@patch("ui.views_bridge.get_status", side_effect=_ready_status)
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

    def test_attivita_list(self, _mock_status) -> None:
        resp = self.client.get(reverse("api_bridge_attivita_list"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["commessa"], "B100")
        self.assertIn("status_pills", data[0])

    def test_attivita_detail(self, _mock_status) -> None:
        resp = self.client.get(
            reverse("api_bridge_attivita_detail", kwargs={"pk": self.att.pk})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["orm_id"], self.att.pk)

    def test_jobs(self, _mock_status) -> None:
        resp = self.client.get(reverse("api_bridge_jobs"))
        self.assertEqual(resp.status_code, 200)
        jobs = resp.json()["data"]
        self.assertEqual(jobs[0]["commessa"], "B100")

    def test_dataset(self, _mock_status) -> None:
        resp = self.client.get(reverse("api_bridge_dataset"))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["data"]
        self.assertIn("attivita", payload)
        self.assertIn("inventario", payload)
        self.assertIn("stabilimenti", payload)

    def test_dataset_filters_attivita_by_user_plant(self, _mock_status) -> None:
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

    def test_dataset_plant_filter_is_case_insensitive(self, _mock_status) -> None:
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

    def test_dataset_without_profile_returns_all_activities(self, _mock_status) -> None:
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

    def test_dataset_keeps_full_inventario_when_filtering_attivita(
        self, _mock_status
    ) -> None:
        valbrembo = Stabilimento.objects.create(nome="Valbrembo", colore="#00aa00")
        UserProfile.objects.update_or_create(
            user=self.user, defaults={"stabilimento": valbrembo}
        )
        TestaItem.objects.create(uid="OV-T-SCHIO", model="TP60", plant="Schio")

        resp = self.client.get(reverse("api_bridge_dataset"))
        inventario = resp.json()["data"]["inventario"]
        uids = {row["uid"] for row in inventario.get("Testa", [])}
        self.assertIn("OV-T-SCHIO", uids)


@patch("ui.views_bridge.get_status", side_effect=_ready_status)
class BridgeInventoryWriteTest(TestCase):
    def setUp(self) -> None:
        self.user = create_writer("tablet-BR1", password="pass")
        self.client = Client()
        self.client.login(username="tablet-BR1", password="pass")
        TestaItem.objects.create(uid="OV-T1", model="TP60", plant="Schio")

    def test_create_component_at_user_plant(self, _mock_status) -> None:
        resp = self.client.post(
            reverse("api_bridge_inventario_create", kwargs={"table": "Testa"}),
            data=json.dumps({"uid": "OV-T9", "model": "TP60", "plant": "Valbrembo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        item = TestaItem.objects.get(uid="OV-T9")
        self.assertEqual(item.plant, "Valbrembo")
        self.assertEqual(item.model, "TP60")

    def test_move_component_to_user_plant(self, _mock_status) -> None:
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

    def test_toggle_maintenance_on_testa(self, _mock_status) -> None:
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

    def test_create_does_not_overwrite_existing_uid(self, _mock_status) -> None:
        resp = self.client.post(
            reverse("api_bridge_inventario_create", kwargs={"table": "Testa"}),
            data=json.dumps({"uid": "OV-T1", "model": "TP70", "plant": "Valbrembo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertFalse(resp.json().get("ok", True))


class BridgeReadNotReadyTest(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("blocked", password="pass")
        self.client = Client()
        self.client.login(username="blocked", password="pass")

    @patch("ui.views_bridge.get_status", return_value=ImportStatus.LOADING)
    def test_read_blocked_when_not_ready(self, _mock_status) -> None:
        resp = self.client.get(reverse("api_bridge_attivita_list"))
        self.assertEqual(resp.status_code, 503)


@patch("ui.views_bridge.get_status", side_effect=_ready_status)
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

    def test_update_attivita(self, _mock_status) -> None:
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
        self.assertTrue(MppSyncQueue.objects.filter(operation="update_task").exists())

    def test_clear_actual_finish(self, _mock_status) -> None:
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
        entry = MppSyncQueue.objects.get(operation="update_task")
        payload = json.loads(entry.payload_json)
        self.assertIn("actual_finish", payload)
        self.assertIsNone(payload["actual_finish"])

    def test_delete_attivita(self, _mock_status) -> None:
        resp = self.client.delete(
            reverse(
                "api_bridge_attivita_delete",
                kwargs={"pk": self.att.pk},
            )
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Attivita.objects.filter(pk=self.att.pk).exists())

    def test_create_job(self, _mock_status) -> None:
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


class BridgeImportTest(TestCase):
    def setUp(self) -> None:
        self.user = create_writer("importer", password="pass")
        self.client = Client()
        self.client.login(username="importer", password="pass")

    @patch("ui.services.bridge_import.trigger_reimport")
    @patch("ui.services.bridge_import.reset_app_dataset")
    @patch("ui.services.bridge_import.get_status", return_value=ImportStatus.READY)
    def test_import_accepted(self, _status, _reset, _trigger) -> None:
        resp = self.client.post(
            reverse("api_bridge_import"),
            data=json.dumps({"force": True}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 202)
        self.assertTrue(resp.json()["accepted"])
        _reset.assert_called_once()
        _trigger.assert_called_once()

    @patch("ui.services.bridge_import.get_status", return_value=ImportStatus.LOADING)
    def test_import_rejected_when_loading(self, _status) -> None:
        resp = self.client.post(
            reverse("api_bridge_import"),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertFalse(resp.json()["accepted"])

    @patch("ui.services.bridge_import.get_status", return_value=ImportStatus.READY)
    def test_import_rejected_with_pending_queue(self, _status) -> None:
        MppSyncQueue.objects.create(operation="update_task", payload_json="{}")
        resp = self.client.post(
            reverse("api_bridge_import"),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)

    @patch("ui.views_bridge.get_status", return_value=ImportStatus.WRITING)
    def test_write_blocked_during_writing(self, _status) -> None:
        att = Attivita.objects.create(
            commessa="X",
            item=1,
            activity="A",
            data_inizio=date(2026, 1, 1),
            data_fine=date(2026, 1, 2),
        )
        resp = self.client.put(
            reverse("api_bridge_attivita_update", kwargs={"pk": att.pk}),
            data=json.dumps({"plant": "P1"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)
