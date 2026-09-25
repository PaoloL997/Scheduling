"""Tests for the per-category resources carried by ``/api/gantt/``.

The home kanban draws every assigned resource as a chip, grouped by category,
and flags the ones that must be moved from another plant.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from ui.models import Attivita, ProblemaAttivita, RisorsaAttivita
from ui.tests.helpers import create_reader


class GanttResourcesPayloadTest(TestCase):
    """``resources_by_tipo`` and ``moving_resources`` on each task."""

    def setUp(self) -> None:
        self.att = Attivita.objects.create(
            commessa="25078",
            item="02-SNC",
            activity="produzione",
            data_inizio=date(2026, 9, 25),
            data_fine=date(2026, 10, 8),
            stabilimento="Valbrembo",
        )
        for tipo, uid in (
            ("testa", "H13"),
            ("generatore", "WO412"),
            ("mandrino", "C160"),
            ("mandrino", "C178"),
        ):
            RisorsaAttivita.objects.create(attivita=self.att, tipo=tipo, risorsa_id=uid)
        self.client.force_login(create_reader())

    def _task(self) -> dict:
        tasks = self.client.get(reverse("api_gantt")).json()["tasks"]
        return next(t for t in tasks if t["orm_id"] == self.att.pk)

    def test_resources_grouped_by_category(self) -> None:
        by_tipo = self._task()["resources_by_tipo"]
        self.assertEqual(by_tipo["testa"], ["H13"])
        self.assertEqual(by_tipo["generatore"], ["WO412"])
        self.assertEqual(sorted(by_tipo["mandrino"]), ["C160", "C178"])
        self.assertEqual(by_tipo["tastatore"], [])
        self.assertEqual(by_tipo["maschera"], [])

    def test_only_open_spostamento_marks_a_resource_as_moving(self) -> None:
        ProblemaAttivita.objects.create(
            attivita=self.att,
            tipo=ProblemaAttivita.TIPO_SPOSTAMENTO,
            risorsa_tipo="generatore",
            risorsa_uid="WO412",
            descrizione="WO412 deve essere spostato",
        )
        ProblemaAttivita.objects.create(
            attivita=self.att,
            tipo=ProblemaAttivita.TIPO_SPOSTAMENTO,
            risorsa_tipo="mandrino",
            risorsa_uid="C160",
            descrizione="già spostato",
            risolto=True,
        )
        ProblemaAttivita.objects.create(
            attivita=self.att,
            tipo=ProblemaAttivita.TIPO_ACQUISTO,
            risorsa_tipo="tastatore",
            descrizione="manca un tastatore",
        )
        self.assertEqual(self._task()["moving_resources"], ["WO412"])

    def test_no_moving_resources_by_default(self) -> None:
        self.assertEqual(self._task()["moving_resources"], [])
