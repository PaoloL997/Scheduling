"""Tests for the three access layers: admin, scrittura, lettura."""

from __future__ import annotations

import json

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from ui.models import (
    GeneratoreItem,
    Job,
    Responsabile,
    Stabilimento,
    TipoAttivita,
    UserProfile,
)
from ui.permissions import (
    get_ruolo,
    has_write_access,
    is_admin_user,
    sync_user_flags,
)
from ui.tests.helpers import create_admin, create_reader, create_writer


class UserProfileRuoloTest(TestCase):
    """Role storage, defaults and derived helpers."""

    def test_new_user_gets_read_only_profile(self) -> None:
        user = User.objects.create_user("nuovo", password="pass")
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.ruolo, UserProfile.RUOLO_LETTURA)
        self.assertFalse(profile.can_write)
        self.assertFalse(profile.is_admin)

    def test_superuser_gets_admin_profile(self) -> None:
        user = User.objects.create_superuser("root", "root@esempio.it", "pass")
        self.assertEqual(user.profile.ruolo, UserProfile.RUOLO_ADMIN)
        self.assertTrue(user.profile.is_admin)

    def test_scrittura_can_write_but_is_not_admin(self) -> None:
        profile = create_writer("w1").profile
        self.assertTrue(profile.can_write)
        self.assertFalse(profile.is_admin)

    def test_helpers_report_the_layer(self) -> None:
        self.assertEqual(get_ruolo(create_reader("r1")), UserProfile.RUOLO_LETTURA)
        self.assertEqual(get_ruolo(create_writer("w2")), UserProfile.RUOLO_SCRITTURA)
        self.assertEqual(get_ruolo(create_admin("a1")), UserProfile.RUOLO_ADMIN)

        self.assertFalse(has_write_access(create_reader("r2")))
        self.assertTrue(has_write_access(create_writer("w3")))
        self.assertTrue(has_write_access(create_admin("a2")))

        self.assertFalse(is_admin_user(create_writer("w4")))
        self.assertTrue(is_admin_user(create_admin("a3")))

    def test_anonymous_user_has_no_layer(self) -> None:
        from django.contrib.auth.models import AnonymousUser

        anon = AnonymousUser()
        self.assertEqual(get_ruolo(anon), "")
        self.assertFalse(has_write_access(anon))
        self.assertFalse(is_admin_user(anon))

    def test_admin_layer_grants_admin_panel_flags(self) -> None:
        user = create_admin("capo")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_demotion_revokes_admin_panel_flags(self) -> None:
        user = create_admin("ex-capo")
        profile = user.profile
        profile.ruolo = UserProfile.RUOLO_LETTURA
        profile.save()
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_sync_user_flags_is_idempotent(self) -> None:
        user = create_writer("stabile")
        self.assertFalse(sync_user_flags(user.profile))

    def test_fixture_user_gets_no_automatic_profile(self) -> None:
        # ``loaddata`` saves with raw=True and brings its own profiles.
        User(username="da-fixture", password="!").save_base(raw=True)
        self.assertFalse(
            UserProfile.objects.filter(user__username="da-fixture").exists()
        )

    def test_fixture_profile_keeps_dumped_flags(self) -> None:
        user = create_writer("staff-storico")
        User.objects.filter(pk=user.pk).update(is_staff=True)
        user.profile.save_base(raw=True)
        user.refresh_from_db()
        self.assertTrue(user.is_staff)


class ReadOnlyApiAccessTest(TestCase):
    """A ``lettura`` account may read but never mutate."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = create_reader("solo-lettura")
        self.client.force_login(self.user)
        GeneratoreItem.objects.create(uid="G100", model="g1", plant="Valbrembo")
        Job.objects.create(commessa="P100", delivery_date="2026-12-31")

    def test_can_open_the_pages(self) -> None:
        for name in ("index", "pianificazione", "inventario", "impostazioni"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_can_read_gantt_api(self) -> None:
        self.assertEqual(self.client.get(reverse("api_gantt")).status_code, 200)

    def test_cannot_add_inventory(self) -> None:
        resp = self.client.post(
            reverse("api_inventario_add", kwargs={"table": "Generatore"}),
            data=json.dumps({"uid": "G200", "model": "g2", "plant": "Valbrembo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["ok"])
        self.assertIn("sola lettura", resp.json()["error"])
        self.assertFalse(GeneratoreItem.objects.filter(uid="G200").exists())

    def test_cannot_delete_inventory(self) -> None:
        resp = self.client.delete(
            reverse(
                "api_inventario_detail",
                kwargs={"table": "Generatore", "uid": "G100"},
            )
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(GeneratoreItem.objects.filter(uid="G100").exists())

    def test_cannot_create_job(self) -> None:
        resp = self.client.post(
            reverse("api_jobs_create"),
            data=json.dumps({"commessa": "P200"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Job.objects.filter(commessa="P200").exists())

    def test_cannot_save_activity(self) -> None:
        resp = self.client.post(
            reverse("api_salva_attivita"),
            data=json.dumps({"commessa": "P100"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_cannot_change_settings(self) -> None:
        resp = self.client.post(
            reverse("api_stabilimenti_create"),
            data=json.dumps({"nome": "Nuovo", "colore": "#123456"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Stabilimento.objects.filter(nome="Nuovo").exists())

    def test_cannot_change_deadline(self) -> None:
        resp = self.client.post(
            reverse("api_commesse_deadline"),
            data=json.dumps({"job_id": "P100", "deadline": "2027-01-01"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_can_read_deadlines(self) -> None:
        self.assertEqual(
            self.client.get(reverse("api_commesse_deadline")).status_code, 200
        )

    def test_cannot_import_inventory_excel(self) -> None:
        resp = self.client.post(reverse("api_inventario_import_excel"))
        self.assertEqual(resp.status_code, 403)


class AnonymousApiAccessTest(TestCase):
    """Mutating endpoints refuse callers without a session."""

    def test_inventory_add_is_refused(self) -> None:
        resp = Client().post(
            reverse("api_inventario_add", kwargs={"table": "Generatore"}),
            data=json.dumps({"uid": "G900", "model": "g9", "plant": "Valbrembo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(GeneratoreItem.objects.filter(uid="G900").exists())


class WriteAccessApiTest(TestCase):
    """A ``scrittura`` account performs the same operations successfully."""

    def setUp(self) -> None:
        self.client = Client()
        self.client.force_login(create_writer("in-scrittura"))

    def test_can_add_inventory(self) -> None:
        resp = self.client.post(
            reverse("api_inventario_add", kwargs={"table": "Generatore"}),
            data=json.dumps({"uid": "G300", "model": "g3", "plant": "Valbrembo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(GeneratoreItem.objects.filter(uid="G300").exists())

    def test_can_create_settings_entries(self) -> None:
        resp = self.client.post(
            reverse("api_responsabili_create"),
            data=json.dumps({"nome": "Mario Rossi"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertTrue(Responsabile.objects.filter(nome="Mario Rossi").exists())

    def test_can_create_activity_type(self) -> None:
        resp = self.client.post(
            reverse("api_tipi_attivita_create"),
            data=json.dumps({"nome": "Collaudo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertTrue(TipoAttivita.objects.filter(nome="Collaudo").exists())


class AdminAccessApiTest(TestCase):
    """An ``admin`` account keeps write access on top of the admin panel."""

    def setUp(self) -> None:
        self.client = Client()
        self.client.force_login(create_admin("capo-reparto"))

    def test_can_add_inventory(self) -> None:
        resp = self.client.post(
            reverse("api_inventario_add", kwargs={"table": "Generatore"}),
            data=json.dumps({"uid": "G400", "model": "g4", "plant": "Valbrembo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)


class AdminPanelAccessTest(TestCase):
    """Only the admin layer may open the Django admin panel."""

    def test_reader_is_redirected_to_admin_login(self) -> None:
        client = Client()
        client.force_login(create_reader("nessun-admin"))
        resp = client.get("/admin/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login/", resp.url)

    def test_writer_is_redirected_to_admin_login(self) -> None:
        client = Client()
        client.force_login(create_writer("scrittore-non-admin"))
        resp = client.get("/admin/")
        self.assertEqual(resp.status_code, 302)

    def test_admin_opens_the_panel(self) -> None:
        client = Client()
        client.force_login(create_admin("vero-admin"))
        resp = client.get("/admin/")
        self.assertEqual(resp.status_code, 200)

    def test_admin_can_change_a_user_role(self) -> None:
        client = Client()
        client.force_login(create_admin("gestore"))
        target = create_reader("da-promuovere")
        resp = client.post(
            reverse("admin:ui_userprofile_change", args=[target.profile.pk]),
            data={
                "user": target.pk,
                "stabilimento": "",
                "ruolo": UserProfile.RUOLO_SCRITTURA,
            },
        )
        self.assertEqual(resp.status_code, 302)
        target.refresh_from_db()
        self.assertEqual(target.profile.ruolo, UserProfile.RUOLO_SCRITTURA)
        self.assertTrue(has_write_access(target))

    def test_user_admin_lists_the_role(self) -> None:
        client = Client()
        client.force_login(create_admin("lista-admin"))
        create_reader("in-elenco")
        resp = client.get(reverse("admin:auth_user_changelist"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Ruolo", resp.content.decode())


class PermissionTemplateContextTest(TestCase):
    """Templates hide the write controls a read-only account cannot use."""

    def test_reader_page_is_marked_readonly(self) -> None:
        client = Client()
        client.force_login(create_reader("lettore-ui"))
        html = client.get(reverse("pianificazione")).content.decode()
        self.assertIn('class="is-readonly"', html)
        self.assertIn("window.CAN_WRITE = false", html)
        self.assertNotIn("Aggiungi attività</button>", html)
        self.assertIn("Sola lettura", html)

    def test_writer_page_keeps_the_controls(self) -> None:
        client = Client()
        client.force_login(create_writer("scrittore-ui"))
        html = client.get(reverse("pianificazione")).content.decode()
        self.assertNotIn('class="is-readonly"', html)
        self.assertIn("window.CAN_WRITE = true", html)
        self.assertIn("Aggiungi attività</button>", html)

    def test_reader_inventory_hides_add_and_delete(self) -> None:
        GeneratoreItem.objects.create(uid="G500", model="g5", plant="Valbrembo")
        client = Client()
        client.force_login(create_reader("lettore-inv"))
        html = client.get(reverse("inventario")).content.decode()
        self.assertIn('data-uid="G500"', html)
        self.assertNotIn('id="btn-add"', html)
        self.assertNotIn("deleteRow('Generatore', 'G500'", html)

    def test_writer_inventory_shows_add_and_delete(self) -> None:
        GeneratoreItem.objects.create(uid="G501", model="g5", plant="Valbrembo")
        client = Client()
        client.force_login(create_writer("scrittore-inv"))
        html = client.get(reverse("inventario")).content.decode()
        self.assertIn('id="btn-add"', html)
        self.assertIn("deleteRow('Generatore', 'G501'", html)

    def test_reader_settings_hide_edit_buttons(self) -> None:
        Stabilimento.objects.create(nome="Valbrembo", colore="#112233")
        client = Client()
        client.force_login(create_reader("lettore-imp"))
        html = client.get(reverse("impostazioni")).content.decode()
        self.assertIn('id="section-stabilimenti"', html)
        self.assertNotIn("openModal('stabilimento', null)", html)
        self.assertNotIn("confirmDelete('stabilimento'", html)

    def test_admin_sees_the_admin_panel_link(self) -> None:
        client = Client()
        client.force_login(create_admin("admin-ui"))
        html = client.get(reverse("index")).content.decode()
        self.assertIn('id="user-modal-admin-link"', html)
        self.assertIn("Amministratore", html)

    def test_writer_does_not_see_the_admin_panel_link(self) -> None:
        client = Client()
        client.force_login(create_writer("scrittore-nav"))
        html = client.get(reverse("index")).content.decode()
        self.assertNotIn('id="user-modal-admin-link"', html)


class BridgePermissionTest(TestCase):
    """The tablet bridge exposes and enforces the same layers."""

    def setUp(self) -> None:
        self.client = Client()

    def test_auth_me_reports_the_layer(self) -> None:
        create_writer("tablet-writer", password="secret")
        self.client.login(username="tablet-writer", password="secret")
        data = self.client.get(reverse("api_bridge_auth_me")).json()["data"]
        self.assertEqual(data["ruolo"], UserProfile.RUOLO_SCRITTURA)
        self.assertTrue(data["can_write"])
        self.assertFalse(data["is_admin"])

    def test_login_payload_reports_read_only(self) -> None:
        create_reader("tablet-reader", password="secret")
        resp = self.client.post(
            reverse("api_bridge_auth_login"),
            data=json.dumps({"username": "tablet-reader", "password": "secret"}),
            content_type="application/json",
        )
        data = resp.json()["data"]
        self.assertEqual(data["ruolo"], UserProfile.RUOLO_LETTURA)
        self.assertFalse(data["can_write"])

    def test_reader_cannot_write_through_the_bridge(self) -> None:
        self.client.force_login(create_reader("tablet-solo-lettura"))
        resp = self.client.post(
            reverse("api_bridge_inventario_create", kwargs={"table": "Testa"}),
            data=json.dumps({"uid": "OV-T9", "model": "TP60", "plant": "Valbrembo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["ok"])


class RegistrationRuoloTest(TestCase):
    """Accounts created from the public form start read-only."""

    def setUp(self) -> None:
        Stabilimento.objects.create(nome="Valbrembo", colore="#112233")

    def test_registered_user_is_read_only(self) -> None:
        resp = self.client.post(
            reverse("register"),
            data={
                "first_name": "Anna",
                "last_name": "Bianchi",
                "username": "anna.bianchi",
                "email": "anna@esempio.it",
                "stabilimento": "Valbrembo",
                "password1": "SicuraPass123!",
                "password2": "SicuraPass123!",
            },
        )
        self.assertEqual(resp.status_code, 302)
        profile = User.objects.get(username="anna.bianchi").profile
        self.assertEqual(profile.ruolo, UserProfile.RUOLO_LETTURA)
        self.assertEqual(profile.stabilimento.nome, "Valbrembo")
