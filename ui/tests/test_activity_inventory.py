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

from ui.models import (
    Attivita,
    ConfigurazioneTesta,
    GeneratoreCompatibile,
    GeneratoreItem,
    MandrinoItem,
    MascheraItem,
    RisorsaAttivita,
    TastatoreItem,
    TestaItem,
)
from ui.services.activity_inventory import get_available_resources
from ui.services.resource_conflicts import candidate_window, occupation_window
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


class Tp60CustomPreselectionTest(TestCase):
    """A TP60 Custom activity starts with no consumable ticked."""

    def setUp(self) -> None:
        cfg = ConfigurazioneTesta.objects.create(
            modello_testa="TP60",
            min_mandrini=2,
            min_tastatori=1,
            min_maschere=1,
            min_generatori=1,
        )
        GeneratoreCompatibile.objects.create(
            configurazione=cfg, modello_generatore="P6"
        )
        TestaItem.objects.create(uid="H1", model="TP60", plant="Valbrembo")
        GeneratoreItem.objects.create(uid="G1", model="P6", plant="Valbrembo")
        MandrinoItem.objects.create(uid="M1", diameter="13.0", plant="Valbrembo")
        MandrinoItem.objects.create(uid="M2", diameter="13.5", plant="Valbrembo")
        TastatoreItem.objects.create(uid="T1", diameter="13.0", plant="Valbrembo")
        MascheraItem.objects.create(uid="MA1", pitch="25", plant="Valbrembo")

    def _result(self, *, tp60_custom: bool) -> dict:
        return get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=START,
            data_fine=FINISH,
            bore_min=12.0,
            bore_max=14.0,
            bore_mid=13.0,
            tube_pitch=25.0,
            modello_testa="TP60",
            tp60_custom=tp60_custom,
        )

    def test_consumables_are_preselected_without_the_flag(self) -> None:
        result = self._result(tp60_custom=False)
        self.assertEqual(result["pre_mandrini"], {"M1", "M2"})
        self.assertEqual(result["pre_tastatori"], {"T1"})
        self.assertEqual(result["pre_maschere"], {"MA1"})

    def test_consumables_are_not_preselected_with_the_flag(self) -> None:
        result = self._result(tp60_custom=True)
        self.assertEqual(result["pre_mandrini"], set())
        self.assertEqual(result["pre_tastatori"], set())
        self.assertEqual(result["pre_maschere"], set())

    def test_head_and_generator_are_still_preselected(self) -> None:
        result = self._result(tp60_custom=True)
        self.assertEqual(result["pre_testa"], {"H1"})
        self.assertEqual(result["pre_generatori"], {"G1"})

    def test_consumables_stay_selectable_by_hand(self) -> None:
        """Only the tick is dropped: the lists still offer every item."""
        result = self._result(tp60_custom=True)
        self.assertEqual([r["uid"] for r in result["mandrini"]], ["M1", "M2"])
        self.assertEqual([r["uid"] for r in result["tastatori"]], ["T1"])
        self.assertEqual([r["uid"] for r in result["maschere"]], ["MA1"])
        self.assertEqual(
            result["min_requirements"],
            {
                "teste": 1,
                "mandrini": 0,
                "tastatori": 0,
                "maschere": 0,
                "generatori": 1,
            },
        )
class StartedActivityUsesItsRealWindowTest(TestCase):
    """An activity under way is offered what is free from its *actual* start.

    Reproduces the reported case: WO411 came off a qualifica that closed on
    22/09 and the next activity was planned to start that same day, so the
    inclusive overlap rule counted the generator as busy.  The activity really
    started on 23/09, which is when it takes its resources — and when the save
    validates them — so the picker must ask about that day, not the planned one.
    """

    PLANNED_START = date(2026, 9, 22)
    PLANNED_FINISH = date(2026, 9, 25)

    def setUp(self) -> None:
        cfg = ConfigurazioneTesta.objects.create(modello_testa="TP60")
        for modello in ("P406", "P6"):
            GeneratoreCompatibile.objects.create(
                configurazione=cfg, modello_generatore=modello
            )
        TestaItem.objects.create(uid="H02", model="TP60", plant="Ricengo")
        GeneratoreItem.objects.create(uid="WO411", model="P406", plant="Ricengo")
        GeneratoreItem.objects.create(uid="WO413", model="P6", plant="Ricengo")

        # The previous job released WO411 on the planned start day.
        closed = Attivita.objects.create(
            commessa="25079",
            item="02",
            activity="qualifica",
            data_inizio=date(2026, 9, 20),
            data_fine=self.PLANNED_START,
            actual_start=date(2026, 9, 20),
            actual_finish=self.PLANNED_START,
            stabilimento="Ricengo",
            modello_testa="TP60",
        )
        RisorsaAttivita.objects.create(
            attivita=closed, tipo="generatore", risorsa_id="WO411"
        )

        self.att = Attivita.objects.create(
            commessa="25108",
            item="01",
            activity="qualifica",
            data_inizio=self.PLANNED_START,
            data_fine=self.PLANNED_FINISH,
            stabilimento="Ricengo",
            modello_testa="TP60",
        )
        RisorsaAttivita.objects.create(
            attivita=self.att, tipo="generatore", risorsa_id="WO413"
        )

    def _generatori(self) -> list[str]:
        result = get_available_resources(
            stabilimento="Ricengo",
            data_inizio=self.PLANNED_START,
            data_fine=self.PLANNED_FINISH,
            modello_testa="TP60",
            exclude_activity_id=self.att.pk,
            strict_requirements=True,
            always_include={"generatori": ["WO413"]},
        )
        return [r["uid"] for r in result["generatori"]]

    def _start_today(self) -> None:
        self.att.actual_start = date(2026, 9, 23)
        self.att.save(update_fields=["actual_start"])

    def test_generator_freed_on_the_planned_start_day_is_offered(self) -> None:
        self._start_today()
        self.assertIn("WO411", self._generatori())

    def test_picker_window_matches_the_one_the_save_validates(self) -> None:
        self._start_today()
        self.assertEqual(
            candidate_window(self.PLANNED_START, self.PLANNED_FINISH, self.att),
            occupation_window(self.att),
        )

    def test_not_started_activity_keeps_its_planned_window(self) -> None:
        self.assertEqual(
            candidate_window(self.PLANNED_START, self.PLANNED_FINISH, self.att),
            (self.PLANNED_START, self.PLANNED_FINISH),
        )
        self.assertNotIn("WO411", self._generatori())
