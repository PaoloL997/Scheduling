"""Measures are shown and stored with the dot, whatever the source."""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import openpyxl
from django.contrib.auth.models import User
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from ui.models import Attivita, Job, MandrinoItem, MascheraItem
from ui.tests.helpers import create_writer
from ui.views import pianificazione


class PianificazioneMeasuresNotLocalisedTest(TestCase):
    """The Italian locale must not turn 13.886 into 13,886 in the template."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="plan", password="p")
        Job.objects.create(commessa="C001", job_id="C001")
        Attivita.objects.create(
            commessa="C001",
            item="01",
            activity="qualifica",
            modello_testa="TP60",
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 3),
            bore_min=Decimal("13.886"),
            bore_max=Decimal("15.774"),
            bore_mid=Decimal("14.83"),
            tube_pitch=Decimal("25.4"),
        )

    def test_bore_attributes_use_the_dot(self) -> None:
        request = self.factory.get(reverse("pianificazione"))
        request.user = self.user
        html = pianificazione(request).content.decode()
        self.assertIn('data-bore-min="13.886"', html)
        self.assertIn('data-bore-max="15.774"', html)
        self.assertIn('data-bore-mid="14.83"', html)
        self.assertIn('data-tube-pitch="25.4"', html)
        self.assertNotIn("13,886", html)


class ExcelImportMeasuresWithDotTest(TestCase):
    """Rows imported from Excel store their measures with the dot."""

    def setUp(self) -> None:
        self.client = Client()
        self.client.force_login(create_writer("importatore"))

    def test_import_normalises_comma_measures(self) -> None:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["categoria", "uid", "misura", "stabilimento", "note"])
        ws.append(["Mandrino", "C901", "20,5", "Valbrembo", "22,5"])
        ws.append(["Maschera", "M901", "49,21", "Schio", "147,6"])
        ws.append(["Mandrino", "C902", 14.83, "Valbrembo", None])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = "inventario.xlsx"

        resp = self.client.post(reverse("api_inventario_import_excel"), {"file": buf})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["imported"], 3)
        c901 = MandrinoItem.objects.get(uid="C901")
        self.assertEqual((c901.diameter, c901.notes), ("20.5", "22.5"))
        m901 = MascheraItem.objects.get(uid="M901")
        self.assertEqual((m901.pitch, m901.center_distance), ("49.21", "147.6"))
        self.assertEqual(MandrinoItem.objects.get(uid="C902").diameter, "14.83")
