"""Tests for the operations audit log (record_operation and read endpoint)."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from ui.models import (
    Attivita,
    ConfigurazioneTesta,
    Job,
    OperationLog,
    RisorsaAttivita,
    Stabilimento,
    TestaItem,
)
from ui.services.inventory import patch_resource
from ui.services.operation_log import record_operation
from ui.services.overlap_reassign import auto_resolve_resource_overlaps
from ui.services.problem_detection import detect_and_save_problems
from ui.services.scan_substitution import scan_resource
from ui.tests.helpers import assign_minimum_kit, create_reader, create_writer
from ui.views import (
    api_attivita_delete,
    api_inventario_add,
    api_inventario_detail,
    api_jobs_create,
    api_operation_log,
    api_overlap_reschedule_accept,
    api_overlap_reschedule_reject,
    api_stabilimenti_create,
    api_tasks_update,
)
from ui.views_attivita import api_salva_attivita


class RecordOperationTest(TestCase):
    """Unit tests for the single write helper."""

    def test_explicit_sistema_source_has_no_user(self) -> None:
        record_operation(
            azione="sistema_test",
            descrizione="Evento automatico",
            source=OperationLog.SOURCE_SISTEMA,
        )
        entry = OperationLog.objects.get(azione="sistema_test")
        self.assertEqual(entry.source, OperationLog.SOURCE_SISTEMA)
        self.assertIsNone(entry.user)
        self.assertEqual(entry.username, "Sistema")

    def test_source_derived_from_bridge_path(self) -> None:
        request = RequestFactory().put("/api/bridge/attivita/1/update/")
        request.user = create_writer("tabletuser")
        record_operation(request, azione="attivita_modifica", descrizione="x")
        entry = OperationLog.objects.get(azione="attivita_modifica")
        self.assertEqual(entry.source, OperationLog.SOURCE_TABLET)
        self.assertEqual(entry.username, "tabletuser")

    def test_source_derived_from_web_path(self) -> None:
        request = RequestFactory().put("/api/tasks/update/")
        request.user = create_writer("webuser")
        record_operation(request, azione="attivita_modifica", descrizione="x")
        entry = OperationLog.objects.get(azione="attivita_modifica")
        self.assertEqual(entry.source, OperationLog.SOURCE_WEB)

    def test_persistence_failure_is_swallowed(self) -> None:
        with patch.object(
            OperationLog.objects, "create", side_effect=RuntimeError("boom")
        ):
            with self.assertLogs("ui.services.operation_log", level="ERROR"):
                record_operation(
                    azione="sistema_test", descrizione="x", source="sistema"
                )
        self.assertFalse(OperationLog.objects.filter(azione="sistema_test").exists())


class ActivityLogTest(TestCase):
    """Web-side activity operations."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = create_writer("planner", password="pass")
        self.att = Attivita.objects.create(
            commessa="C100",
            item=1,
            activity="A1",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
        )

    def test_api_tasks_update_logs_web_source(self) -> None:
        request = self.factory.put(
            "/api/tasks/update/",
            data=json.dumps({"orm_id": self.att.pk, "responsible": "Rossi"}),
            content_type="application/json",
        )
        request.user = self.user
        resp = api_tasks_update(request)
        self.assertEqual(resp.status_code, 200)
        entry = OperationLog.objects.get(azione="attivita_modifica")
        self.assertEqual(entry.source, OperationLog.SOURCE_WEB)
        self.assertIn("responsabile", entry.descrizione)

    def test_api_attivita_delete_logs_identity_before_delete(self) -> None:
        request = self.factory.delete(
            "/api/tasks/delete/",
            data=json.dumps({"orm_id": self.att.pk}),
            content_type="application/json",
        )
        request.user = self.user
        resp = api_attivita_delete(request)
        self.assertEqual(resp.status_code, 200)
        entry = OperationLog.objects.get(azione="attivita_elimina")
        self.assertEqual(entry.source, OperationLog.SOURCE_WEB)
        self.assertIn(self.att.activity_id, entry.descrizione)

    def test_api_salva_attivita_logs_creation(self) -> None:
        Job.objects.create(commessa="C200")
        from ui.models import TipoAttivita

        TipoAttivita.objects.create(nome="Qualifica")
        request = self.factory.post(
            "/api/attivita/salva/",
            data=json.dumps(
                {
                    "commessa": "C200",
                    "item": 1,
                    "attivita": "Qualifica",
                    "data_inizio": "2026-06-10",
                    "data_fine": "2026-06-12",
                    "risorse": {},
                }
            ),
            content_type="application/json",
        )
        request.user = self.user
        resp = api_salva_attivita(request)
        self.assertEqual(resp.status_code, 200)
        entry = OperationLog.objects.get(azione="attivita_crea")
        self.assertEqual(entry.source, OperationLog.SOURCE_WEB)


class BridgeActivityLogTest(TestCase):
    """Tablet-side (bridge) activity operations, including delegated ones."""

    def setUp(self) -> None:
        self.user = create_writer("tablet1", password="pass")
        self.client = Client()
        self.client.login(username="tablet1", password="pass")
        self.att = Attivita.objects.create(
            commessa="W100",
            item=1,
            activity="Test",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
        )

    def test_bridge_update_logs_tablet_source(self) -> None:
        assign_minimum_kit(self.att)
        resp = self.client.put(
            reverse("api_bridge_attivita_update", kwargs={"pk": self.att.pk}),
            data=json.dumps({"actual_start": "2026-06-02"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        entry = OperationLog.objects.get(azione="attivita_modifica")
        self.assertEqual(entry.source, OperationLog.SOURCE_TABLET)

    def test_bridge_delete_logs_tablet_source(self) -> None:
        resp = self.client.delete(
            reverse("api_bridge_attivita_delete", kwargs={"pk": self.att.pk})
        )
        self.assertEqual(resp.status_code, 200)
        entry = OperationLog.objects.get(azione="attivita_elimina")
        self.assertEqual(entry.source, OperationLog.SOURCE_TABLET)

    def test_bridge_create_delegated_view_logs_tablet_source(self) -> None:
        """api_salva_attivita is shared; the delegated bridge path must still
        attribute the log entry to the tablet, proving path-derived source
        works through delegation."""
        from ui.models import TipoAttivita

        TipoAttivita.objects.create(nome="Saldatura")
        Job.objects.create(commessa="W200")
        resp = self.client.post(
            reverse("api_bridge_attivita_create"),
            data=json.dumps(
                {
                    "commessa": "W200",
                    "item": 1,
                    "attivita": "Saldatura",
                    "data_inizio": "2026-06-10",
                    "data_fine": "2026-06-12",
                    "risorse": {},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        entry = OperationLog.objects.get(azione="attivita_crea")
        self.assertEqual(entry.source, OperationLog.SOURCE_TABLET)


class ScanSubstitutionLogTest(TestCase):
    """Scan confirmation logs a tablet entry; robbing another activity logs
    a separate system entry."""

    def setUp(self) -> None:
        TestaItem.objects.create(uid="T1", model="HT200", plant="Valbrembo")
        from ui.models import MandrinoItem

        MandrinoItem.objects.create(uid="M102", diameter="22", plant="Valbrembo")
        MandrinoItem.objects.create(uid="M117", diameter="23,5", plant="Valbrembo")
        self.att = Attivita.objects.create(
            commessa="C100",
            item=1,
            activity="Saldatura",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            stabilimento="Valbrembo",
            modello_testa="HT200",
            bore_min=20,
            bore_max=25,
            bore_mid=22.5,
        )
        RisorsaAttivita.objects.create(attivita=self.att, tipo="testa", risorsa_id="T1")
        RisorsaAttivita.objects.create(
            attivita=self.att, tipo="mandrino", risorsa_id="M102"
        )

    def test_confirmed_scan_logs_tablet_entry(self) -> None:
        create_writer("scanner", password="pass")
        client = Client()
        client.login(username="scanner", password="pass")
        resp = client.post(
            reverse("api_bridge_attivita_scan", kwargs={"pk": self.att.pk}),
            data=json.dumps({"tipo": "mandrino", "codice": "M117", "conferma": True}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        entry = OperationLog.objects.get(azione="attivita_sostituzione_tablet")
        self.assertEqual(entry.source, OperationLog.SOURCE_TABLET)

    def test_robbing_another_activity_logs_system_entry(self) -> None:
        TestaItem.objects.create(uid="T2", model="HT200", plant="Valbrembo")
        other = Attivita.objects.create(
            commessa="C200",
            item=1,
            activity="Saldatura",
            data_inizio=date(2026, 6, 2),
            data_fine=date(2026, 6, 4),
            stabilimento="Valbrembo",
            modello_testa="HT200",
            bore_min=20,
            bore_max=25,
            bore_mid=22.5,
        )
        RisorsaAttivita.objects.create(attivita=other, tipo="testa", risorsa_id="T2")
        RisorsaAttivita.objects.create(
            attivita=other, tipo="mandrino", risorsa_id="M117"
        )
        scan_resource(self.att.pk, tipo="mandrino", codice="M117", conferma=True)
        entry = OperationLog.objects.get(
            azione="sistema_sostituzione", riferimento="C200/1/Saldatura"
        )
        self.assertEqual(entry.source, OperationLog.SOURCE_SISTEMA)

    def test_robbing_without_a_spare_is_not_logged(self) -> None:
        """An uncovered robbed activity is a detected problem, not a resolution."""
        TestaItem.objects.create(uid="T2", model="HT200", plant="Valbrembo")
        other = Attivita.objects.create(
            commessa="C300",
            item=1,
            activity="Saldatura",
            data_inizio=date(2026, 6, 2),
            data_fine=date(2026, 6, 4),
            stabilimento="Valbrembo",
            modello_testa="HT200",
            # Excludes M102 (diameter 22): only used to filter the spare
            # search for `other`, not to validate the scanned piece itself.
            bore_min=30,
            bore_max=40,
            bore_mid=35,
        )
        RisorsaAttivita.objects.create(attivita=other, tipo="testa", risorsa_id="T2")
        RisorsaAttivita.objects.create(
            attivita=other, tipo="mandrino", risorsa_id="M117"
        )
        scan_resource(self.att.pk, tipo="mandrino", codice="M117", conferma=True)
        self.assertFalse(
            OperationLog.objects.filter(riferimento="C300/1/Saldatura").exists()
        )


class RescheduleLogTest(TestCase):
    """Accept/reject of an auto-computed reschedule proposal (web only)."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = create_writer("planner2", password="pass")
        ConfigurazioneTesta.objects.create(
            modello_testa="TP60",
            min_mandrini=0,
            min_tastatori=0,
            min_maschere=0,
            min_generatori=0,
        )
        TestaItem.objects.create(uid="OV-T1", model="TP60", plant="Valbrembo")
        Job.objects.create(commessa="C205", delivery_date=date(2026, 9, 30))
        self.late = Attivita.objects.create(
            commessa="C101",
            item=1,
            activity="Saldatura",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 10),
            data_fine=date(2026, 8, 19),
            actual_start=date(2026, 8, 17),
        )
        self.blocked = Attivita.objects.create(
            commessa="C205",
            item=2,
            activity="Montaggio",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 20),
            data_fine=date(2026, 8, 29),
        )
        for att in (self.late, self.blocked):
            RisorsaAttivita.objects.create(
                attivita=att, tipo="testa", risorsa_id="OV-T1"
            )
        auto_resolve_resource_overlaps()

    def test_accept_logs_web_entry(self) -> None:
        from ui.services.overlap_reassign import compute_reschedule_proposal

        compute_reschedule_proposal(self.blocked.pk)
        request = self.factory.post(
            f"/api/attivita/{self.blocked.pk}/riprogramma/accetta/"
        )
        request.user = self.user
        resp = api_overlap_reschedule_accept(request, self.blocked.pk)
        self.assertEqual(resp.status_code, 200)
        entry = OperationLog.objects.get(azione="attivita_riprogramma_accetta")
        self.assertEqual(entry.source, OperationLog.SOURCE_WEB)

    def test_reject_logs_web_entry(self) -> None:
        request = self.factory.post(
            f"/api/attivita/{self.blocked.pk}/riprogramma/rifiuta/"
        )
        request.user = self.user
        resp = api_overlap_reschedule_reject(request, self.blocked.pk)
        self.assertEqual(resp.status_code, 200)
        entry = OperationLog.objects.get(azione="attivita_riprogramma_rifiuta")
        self.assertEqual(entry.source, OperationLog.SOURCE_WEB)


class JobsInventarioAnagraficheLogTest(TestCase):
    """Web/tablet-shared jobs, inventory and anagrafica endpoints."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = create_writer("planner3", password="pass")

    def test_jobs_create_web_logs_web_source(self) -> None:
        request = self.factory.post(
            "/api/jobs/create/",
            data=json.dumps({"commessa": "NEWJOB"}),
            content_type="application/json",
        )
        request.user = self.user
        resp = api_jobs_create(request)
        self.assertEqual(resp.status_code, 201)
        entry = OperationLog.objects.get(azione="commessa_crea")
        self.assertEqual(entry.source, OperationLog.SOURCE_WEB)

    def test_jobs_create_delegated_bridge_logs_tablet_source(self) -> None:
        client = Client()
        client.login(username=self.user.username, password="pass")
        resp = client.post(
            reverse("api_bridge_jobs_create"),
            data=json.dumps({"commessa": "TABJOB"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        entry = OperationLog.objects.get(azione="commessa_crea", riferimento="TABJOB")
        self.assertEqual(entry.source, OperationLog.SOURCE_TABLET)

    def test_inventario_add_and_detail_log_entries(self) -> None:
        add_request = self.factory.post(
            "/api/inventario/Generatore/",
            data=json.dumps({"uid": "G900"}),
            content_type="application/json",
        )
        add_request.user = self.user
        resp = api_inventario_add(add_request, "Generatore")
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(OperationLog.objects.filter(azione="inventario_crea").exists())

        patch_request = self.factory.patch(
            "/api/inventario/Generatore/G900/",
            data=json.dumps({"plant": "Osio"}),
            content_type="application/json",
        )
        patch_request.user = self.user
        resp = api_inventario_detail(patch_request, "Generatore", "G900")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            OperationLog.objects.filter(azione="inventario_modifica").exists()
        )

        delete_request = self.factory.delete("/api/inventario/Generatore/G900/")
        delete_request.user = self.user
        resp = api_inventario_detail(delete_request, "Generatore", "G900")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            OperationLog.objects.filter(azione="inventario_elimina").exists()
        )

    def test_anagrafica_stabilimento_create_logs_entry(self) -> None:
        request = self.factory.post(
            "/api/stabilimenti/",
            data=json.dumps({"nome": "Nuovo Stabilimento"}),
            content_type="application/json",
        )
        request.user = self.user
        resp = api_stabilimenti_create(request)
        self.assertEqual(resp.status_code, 201)
        entry = OperationLog.objects.get(azione="anagrafica_crea")
        self.assertIn("Nuovo Stabilimento", entry.descrizione)


class SystemEventsLogTest(TestCase):
    """Automatic problem-detection / auto-resolution events, source=sistema."""

    def test_shortfall_detection_is_not_logged(self) -> None:
        """Detecting a new problem is not an "action": only its resolution is."""
        att = Attivita.objects.create(
            commessa="S100",
            item=1,
            activity="A1",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
        )
        detect_and_save_problems(att, {})
        self.assertFalse(OperationLog.objects.exists())

    def test_stale_shortfall_auto_resolve_logs_system_entry(self) -> None:
        ConfigurazioneTesta.objects.create(
            modello_testa="X1",
            min_mandrini=2,
            min_tastatori=0,
            min_maschere=0,
            min_generatori=0,
        )
        att = Attivita.objects.create(
            commessa="S200",
            item=1,
            activity="A1",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            modello_testa="X1",
        )
        detect_and_save_problems(
            att, {"teste": ["T1"], "mandrini": ["M1"], "generatori": ["G1"]}
        )
        self.assertFalse(OperationLog.objects.exists())

        detect_and_save_problems(
            att, {"teste": ["T1"], "mandrini": ["M1", "M2"], "generatori": ["G1"]}
        )
        entry = OperationLog.objects.get(azione="sistema_problema_risolto")
        self.assertEqual(entry.source, OperationLog.SOURCE_SISTEMA)
        self.assertIsNone(entry.user)

    def test_overlap_auto_swap_logs_system_entry(self) -> None:
        late = Attivita.objects.create(
            commessa="C101",
            item=1,
            activity="Saldatura",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 10),
            data_fine=date(2026, 8, 19),
            actual_start=date(2026, 8, 17),
        )
        blocked = Attivita.objects.create(
            commessa="C205",
            item=2,
            activity="Montaggio",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 20),
            data_fine=date(2026, 8, 29),
        )
        for att in (late, blocked):
            RisorsaAttivita.objects.create(
                attivita=att, tipo="testa", risorsa_id="OV-T1"
            )
        TestaItem.objects.create(uid="OV-T1", model="TP60", plant="Valbrembo")
        TestaItem.objects.create(uid="OV-T2", model="TP60", plant="Valbrembo")
        auto_resolve_resource_overlaps()
        entry = OperationLog.objects.get(azione="sistema_sostituzione")
        self.assertEqual(entry.source, OperationLog.SOURCE_SISTEMA)

    def test_maintenance_swap_logs_system_entry(self) -> None:
        ConfigurazioneTesta.objects.create(
            modello_testa="TP60",
            min_mandrini=0,
            min_tastatori=0,
            min_maschere=0,
            min_generatori=0,
        )
        TestaItem.objects.create(uid="OV-T1", model="TP60", plant="Valbrembo")
        TestaItem.objects.create(uid="OV-T2", model="TP60", plant="Valbrembo")
        att = Attivita.objects.create(
            commessa="C205",
            item=1,
            activity="Montaggio",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 20),
            data_fine=date(2026, 8, 29),
        )
        RisorsaAttivita.objects.create(attivita=att, tipo="testa", risorsa_id="OV-T1")
        patch_resource("Testa", "OV-T1", {"in_manutenzione": True})
        entry = OperationLog.objects.get(azione="sistema_sostituzione")
        self.assertEqual(entry.source, OperationLog.SOURCE_SISTEMA)


class OperationLogPermissionsTest(TestCase):
    """Visibility gating: write access only."""

    def setUp(self) -> None:
        Stabilimento.objects.create(nome="Valbrembo", colore="#112233")
        OperationLog.objects.create(
            username="Sistema",
            source=OperationLog.SOURCE_SISTEMA,
            azione="sistema_test",
            descrizione="Evento di test",
        )

    def test_reader_does_not_see_log_section(self) -> None:
        client = Client()
        client.force_login(create_reader("lettore-log"))
        html = client.get(reverse("impostazioni")).content.decode()
        self.assertNotIn('id="section-log"', html)
        self.assertNotIn("Evento di test", html)

    def test_writer_sees_log_section(self) -> None:
        client = Client()
        client.force_login(create_writer("scrittore-log"))
        html = client.get(reverse("impostazioni")).content.decode()
        self.assertIn('id="section-log"', html)
        self.assertIn("Evento di test", html)

    def test_api_log_forbidden_for_reader(self) -> None:
        client = Client()
        client.force_login(create_reader("lettore-api-log"))
        resp = client.get(reverse("api_operation_log"))
        self.assertEqual(resp.status_code, 403)

    def test_api_log_allowed_for_writer(self) -> None:
        client = Client()
        client.force_login(create_writer("scrittore-api-log"))
        resp = client.get(reverse("api_operation_log"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])


class OperationLogPaginationTest(TestCase):
    """Cursor-based pagination of the read endpoint."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = create_writer("paginator", password="pass")
        for i in range(60):
            OperationLog.objects.create(
                username="Sistema",
                source=OperationLog.SOURCE_SISTEMA,
                azione="sistema_test",
                descrizione=f"Evento {i}",
            )

    def test_first_page_returns_fifty_and_a_cursor(self) -> None:
        request = self.factory.get("/api/log/")
        request.user = self.user
        resp = api_operation_log(request)
        data = json.loads(resp.content)
        self.assertEqual(len(data["items"]), 50)
        self.assertIsNotNone(data["next_before_id"])

    def test_second_page_returns_remainder_with_no_cursor(self) -> None:
        first_request = self.factory.get("/api/log/")
        first_request.user = self.user
        first_data = json.loads(api_operation_log(first_request).content)

        second_request = self.factory.get(
            f"/api/log/?before_id={first_data['next_before_id']}&limit=50"
        )
        second_request.user = self.user
        second_data = json.loads(api_operation_log(second_request).content)
        self.assertEqual(len(second_data["items"]), 10)
        self.assertIsNone(second_data["next_before_id"])

    def test_limit_is_capped_at_200(self) -> None:
        request = self.factory.get("/api/log/?limit=99999")
        request.user = self.user
        resp = api_operation_log(request)
        data = json.loads(resp.content)
        self.assertLessEqual(len(data["items"]), 200)
