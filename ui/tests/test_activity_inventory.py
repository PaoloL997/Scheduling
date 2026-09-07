"""Tests for the resource-availability lists behind «Modifica risorse».

A resource already booked by the activity being edited must always be offered
back, whatever the availability filters say: dropping it from the list used to
wipe the assignment as soon as the picker was confirmed.
"""

from __future__ import annotations

import json
from datetime import date

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from ui.models import Attivita, RisorsaAttivita, TestaItem
from ui.services.activity_inventory import get_available_resources
from ui.views_attivita import api_get_risorse

START = date(2026, 8, 25)
FINISH = date(2026, 9, 1)


def _lookup(rows: list[dict], uid: str) -> dict | None:
    """Return the row for *uid*, or ``None`` when the list does not carry it."""
    return next((r for r in rows if r["uid"] == uid), None)


class AlwaysIncludeAssignedTestaTest(TestCase):
    """The currently-assigned testa survives every availability filter."""

    def setUp(self) -> None:
        self.att = Attivita.objects.create(
            commessa="25078",
            item="02-SNC",
            activity="produzione",
            data_inizio=START,
            data_fine=FINISH,
            stabilimento="Valbrembo",
            modello_testa="TS2000",
        )
        RisorsaAttivita.objects.create(
            attivita=self.att, tipo="testa", risorsa_id="H14"
        )
        self.h14 = TestaItem.objects.create(
            uid="H14", model="TS2000", plant="Valbrembo"
        )

    def _available(self, **kwargs) -> list[dict]:
        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=START,
            data_fine=FINISH,
            modello_testa="TS2000",
            exclude_activity_id=self.att.pk,
            always_include={"teste": ["H14"]},
            **kwargs,
        )
        return result["teste"]

    def test_present_and_first_when_plainly_available(self) -> None:
        rows = self._available()
        self.assertEqual(rows[0]["uid"], "H14")
        self.assertTrue(rows[0]["assigned_current"])
        self.assertIsNone(rows[0]["unavailable_reason"])

    def test_present_when_in_maintenance(self) -> None:
        self.h14.in_manutenzione = True
        self.h14.save(update_fields=["in_manutenzione"])
        row = _lookup(self._available(), "H14")
        self.assertIsNotNone(row)
        self.assertEqual(row["unavailable_reason"], "manutenzione")

    def test_present_when_booked_by_another_activity(self) -> None:
        other = Attivita.objects.create(
            commessa="25078",
            item="02",
            activity="qualifica",
            data_inizio=date(2026, 7, 22),
            data_fine=date(2026, 8, 17),
            actual_start=date(2026, 7, 22),
            actual_finish=date(2026, 9, 4),
            stabilimento="Valbrembo",
        )
        RisorsaAttivita.objects.create(attivita=other, tipo="testa", risorsa_id="H14")

        row = _lookup(self._available(), "H14")
        self.assertIsNotNone(row)
        self.assertEqual(row["unavailable_reason"], "occupata")

    def test_present_when_at_another_plant(self) -> None:
        self.h14.plant = "Ricengo"
        self.h14.save(update_fields=["plant"])
        TestaItem.objects.create(uid="H20", model="TS2000", plant="Valbrembo")

        rows = self._available()
        row = _lookup(rows, "H14")
        self.assertIsNotNone(row)
        self.assertEqual(row["unavailable_reason"], "altro_stabilimento")
        self.assertEqual(row["stab_esterno"], "Ricengo")
        self.assertIn("H20", [r["uid"] for r in rows])

    def test_present_when_model_does_not_match(self) -> None:
        self.h14.model = "TP60"
        self.h14.save(update_fields=["model"])
        row = _lookup(self._available(), "H14")
        self.assertIsNotNone(row)
        self.assertEqual(row["unavailable_reason"], "non_compatibile")

    def test_absent_without_always_include(self) -> None:
        """Without the new parameter the old silent-drop behaviour is visible."""
        self.h14.in_manutenzione = True
        self.h14.save(update_fields=["in_manutenzione"])
        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=START,
            data_fine=FINISH,
            modello_testa="TS2000",
            exclude_activity_id=self.att.pk,
        )
        self.assertIsNone(_lookup(result["teste"], "H14"))


class ModelMatchIsCaseInsensitiveTest(TestCase):
    """Head models differing only by case or spacing still match."""

    def test_iexact_match(self) -> None:
        TestaItem.objects.create(uid="H30", model="TS2000", plant="Valbrembo")
        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=START,
            data_fine=FINISH,
            modello_testa=" ts2000 ",
        )
        self.assertIn("H30", [r["uid"] for r in result["teste"]])


class ApiGetRisorseEditFlowTest(TestCase):
    """The endpoint feeds ``always_include`` from the activity being edited."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="planner", password="p")
        self.att = Attivita.objects.create(
            commessa="25078",
            item="02-SNC",
            activity="produzione",
            data_inizio=START,
            data_fine=FINISH,
            stabilimento="Valbrembo",
            modello_testa="TS2000",
        )
        RisorsaAttivita.objects.create(
            attivita=self.att, tipo="testa", risorsa_id="H14"
        )
        TestaItem.objects.create(
            uid="H14", model="TS2000", plant="Valbrembo", in_manutenzione=True
        )

    def _get(self, **params):
        query = {
            "stabilimento": "Valbrembo",
            "data_inizio": START.isoformat(),
            "data_fine": FINISH.isoformat(),
            "exclude_activity_id": str(self.att.pk),
            **params,
        }
        request = self.factory.get("/api/attivita/risorse/", data=query)
        request.user = self.user
        return json.loads(api_get_risorse(request).content)

    def test_assigned_head_is_returned_even_in_maintenance(self) -> None:
        payload = self._get()
        self.assertTrue(payload["ok"])
        row = _lookup(payload["teste"], "H14")
        self.assertIsNotNone(row)
        self.assertTrue(row["assigned_current"])
        self.assertEqual(row["unavailable_reason"], "manutenzione")

    def test_category_not_emptied_when_the_activity_has_no_head_model(self) -> None:
        """Strict filtering used to return an empty list with nothing selected."""
        self.att.modello_testa = ""
        self.att.save(update_fields=["modello_testa"])
        payload = self._get()
        self.assertTrue(payload["teste"])
        self.assertIsNotNone(_lookup(payload["teste"], "H14"))
