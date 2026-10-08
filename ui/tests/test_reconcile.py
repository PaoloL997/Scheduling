"""Reads never write; every write path reconciles the operational notes."""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.timezone import localdate

from ui.models import (
    Attivita,
    GeneratoreItem,
    Job,
    OperationLog,
    ProblemaAttivita,
    RisorsaAttivita,
    TestaItem,
    TipoAttivita,
)
from ui.services import reconcile as reconcile_module
from ui.services.reconcile import ensure_daily_sync, reconcile, reset_daily_sync


def _snapshot() -> tuple:
    return (
        list(
            ProblemaAttivita.objects.order_by("pk").values_list(
                "pk", "tipo", "risolto", "descrizione"
            )
        ),
        list(
            RisorsaAttivita.objects.order_by("pk").values_list(
                "attivita_id", "tipo", "risorsa_id"
            )
        ),
        OperationLog.objects.count(),
    )


def _writer() -> Client:
    user = User.objects.create_user(username="planner", password="p")
    from ui.permissions import get_or_create_profile

    profile = get_or_create_profile(user)
    profile.ruolo = "scrittura"
    profile.save()
    client = Client()
    client.force_login(user)
    return client


class ReadsNeverWriteTest(TestCase):
    """Pianificazione and the Gantt leave the notes exactly as they found them."""

    def setUp(self) -> None:
        reset_daily_sync()
        self.client = _writer()
        today = localdate()
        TestaItem.objects.create(uid="T1", model="TP60", plant="Valbrembo")
        Job.objects.create(commessa="C1", job_id="C1")
        # Two activities overlapping on the same testa, one started late: a
        # state that would trigger an auto-swap and open notes if reconciled.
        late = Attivita.objects.create(
            commessa="C1",
            item="1",
            activity="Late",
            data_inizio=today - timedelta(days=10),
            data_fine=today - timedelta(days=2),
            actual_start=today - timedelta(days=4),
        )
        blocked = Attivita.objects.create(
            commessa="C1",
            item="2",
            activity="Blocked",
            data_inizio=today - timedelta(days=1),
            data_fine=today + timedelta(days=5),
        )
        for att in (late, blocked):
            RisorsaAttivita.objects.create(attivita=att, tipo="testa", risorsa_id="T1")
        # Stale note that a reconciliation would close.
        ProblemaAttivita.objects.create(
            attivita=blocked,
            tipo=ProblemaAttivita.TIPO_ACQUISTO,
            risorsa_tipo="generatore",
            descrizione="stale",
        )
        # The daily ritardo sync is allowed; run it once so it is not what
        # these assertions measure.
        ensure_daily_sync()

    def test_pianificazione_page_does_not_write(self) -> None:
        before = _snapshot()
        resp = self.client.get(reverse("pianificazione"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(_snapshot(), before)

    def test_gantt_api_does_not_write(self) -> None:
        before = _snapshot()
        resp = self.client.get("/api/gantt/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(_snapshot(), before)

    def test_daily_sync_runs_once_per_day(self) -> None:
        reset_daily_sync()
        self.assertTrue(ensure_daily_sync())
        self.assertFalse(ensure_daily_sync())
        self.assertTrue(ensure_daily_sync(today=localdate() + timedelta(days=1)))
        self.assertIsNotNone(reconcile_module._last_daily_sync)


class WritePathsReconcileTest(TestCase):
    """Each write leaves the notes consistent without any page load."""

    def setUp(self) -> None:
        reset_daily_sync()
        self.client = _writer()
        self.today = localdate()
        TestaItem.objects.create(uid="T1", model="TP60", plant="Valbrembo")
        TestaItem.objects.create(uid="T2", model="TP60", plant="Valbrembo")
        GeneratoreItem.objects.create(uid="G1", model="GX", plant="Valbrembo")
        TipoAttivita.objects.create(nome="Montaggio")
        Job.objects.create(commessa="C1", job_id="C1")

    def test_wizard_save_raises_the_ritardo_note_for_a_past_plan(self) -> None:
        past_start = (self.today - timedelta(days=10)).isoformat()
        past_finish = (self.today - timedelta(days=5)).isoformat()
        resp = self.client.post(
            "/api/attivita/salva/",
            data=json.dumps(
                {
                    "commessa": "C1",
                    "item": "1",
                    "attivita": "Montaggio",
                    "stabilimento": "Valbrembo",
                    "data_inizio": past_start,
                    "data_fine": past_finish,
                    "risorse": {"teste": ["T1"], "generatori": ["G1"]},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        att = Attivita.objects.get(pk=body["id"])
        tipi = set(
            ProblemaAttivita.objects.filter(attivita=att, risolto=False).values_list(
                "tipo", flat=True
            )
        )
        self.assertIn(ProblemaAttivita.TIPO_RITARDO, tipi)
        self.assertEqual(
            body["warning_count"],
            ProblemaAttivita.objects.filter(attivita=att, risolto=False).count(),
        )
        self.assertEqual(body["task"]["state_key"], "in_ritardo")

    def test_deleting_the_late_peer_closes_the_overlap_note(self) -> None:
        late = Attivita.objects.create(
            commessa="C1",
            item="1",
            activity="Late",
            data_inizio=self.today - timedelta(days=10),
            data_fine=self.today - timedelta(days=2),
            actual_start=self.today - timedelta(days=4),
        )
        blocked = Attivita.objects.create(
            commessa="C1",
            item="2",
            activity="Blocked",
            data_inizio=self.today + timedelta(days=1),
            data_fine=self.today + timedelta(days=5),
        )
        for att in (late, blocked):
            RisorsaAttivita.objects.create(attivita=att, tipo="testa", risorsa_id="T1")
        # Only one testa is free (T2) but the swap rule may use it; make the
        # overlap unresolvable so an open note exists.
        TestaItem.objects.filter(uid="T2").update(in_manutenzione=True)
        reconcile()
        self.assertTrue(
            ProblemaAttivita.objects.filter(
                attivita=blocked,
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
                risolto=False,
            ).exists()
        )

        resp = self.client.delete(
            "/api/tasks/delete/",
            data=json.dumps({"orm_id": late.pk}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=blocked,
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
                risolto=False,
            ).exists()
        )

    def test_applying_an_optimization_refreshes_the_notes(self) -> None:
        from ui.services.optimization_apply import apply_optimization_result

        att = Attivita.objects.create(
            commessa="C1",
            item="1",
            activity="Montaggio",
            stabilimento="Valbrembo",
            data_inizio=self.today - timedelta(days=10),
            data_fine=self.today - timedelta(days=5),
        )
        reconcile([att.pk])
        self.assertTrue(
            ProblemaAttivita.objects.filter(
                attivita=att, tipo=ProblemaAttivita.TIPO_RITARDO, risolto=False
            ).exists()
        )
        TestaItem.objects.create(uid="T9", model="TP60", plant="Altrove")

        apply_optimization_result(
            {
                "job_ids": ["C1"],
                "tasks_after": [
                    {
                        "orm_id": att.pk,
                        "activity_id": att.activity_id,
                        "start": (self.today + timedelta(days=3)).isoformat(),
                        "finish": (self.today + timedelta(days=8)).isoformat(),
                        "assigned_resource_ids": ["testa:T9", "generatore:G1"],
                    }
                ],
            }
        )

        open_tipi = set(
            ProblemaAttivita.objects.filter(attivita=att, risolto=False).values_list(
                "tipo", flat=True
            )
        )
        # Moved into the future: the ritardo closes; the testa from another
        # plant raises a spostamento.
        self.assertNotIn(ProblemaAttivita.TIPO_RITARDO, open_tipi)
        self.assertIn(ProblemaAttivita.TIPO_SPOSTAMENTO, open_tipi)
