"""Tests for the inventory availability column (green/red dot + tooltip)."""

from __future__ import annotations

from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from ui.models import (
    Attivita,
    GeneratoreItem,
    MandrinoItem,
    MascheraItem,
    RisorsaAttivita,
    TastatoreItem,
    TestaItem,
)
from ui.services.resource_availability import (
    annotate_inventory_rows,
    availability_cell_html,
    availability_map,
)
from ui.tests.helpers import create_reader

TODAY = date(2026, 9, 9)


def _make_activity(
    commessa: str,
    item: int,
    di: str,
    df: str,
    *,
    actual_start: str | None = None,
    actual_finish: str | None = None,
) -> Attivita:
    """Create a persisted activity within a fixed date range.

    Args:
        commessa: Commessa identifier.
        item: Item number.
        di: ISO planned start date.
        df: ISO planned end date.
        actual_start: Optional ISO actual start.
        actual_finish: Optional ISO actual finish.

    Returns:
        Attivita: The created instance.
    """
    return Attivita.objects.create(
        commessa=commessa,
        item=item,
        activity="Saldatura",
        data_inizio=date.fromisoformat(di),
        data_fine=date.fromisoformat(df),
        actual_start=date.fromisoformat(actual_start) if actual_start else None,
        actual_finish=date.fromisoformat(actual_finish) if actual_finish else None,
    )


def _book(att: Attivita, tipo: str, risorsa_id: str) -> RisorsaAttivita:
    """Persist a single resource booking on an activity.

    Args:
        att: Parent activity.
        tipo: Resource category.
        risorsa_id: Resource identifier.

    Returns:
        RisorsaAttivita: The created booking row.
    """
    return RisorsaAttivita.objects.create(
        attivita=att, tipo=tipo, risorsa_id=risorsa_id
    )


class AvailabilityFreeTest(TestCase):
    """A resource nobody booked is free, with nothing planned ahead."""

    def test_no_booking_at_all(self) -> None:
        info = availability_map("testa", ["T1"], TODAY)["T1"]
        self.assertEqual(info["stato"], "libera")
        self.assertTrue(info["libera"])
        self.assertIsNone(info["prossimo_impegno"])
        self.assertEqual(info["attivita"], [])
        self.assertEqual(info["tooltip"], "Libera oggi.\nNessun impegno pianificato.")

    def test_only_past_bookings(self) -> None:
        att = _make_activity("X001", 1, "2026-08-01", "2026-09-08")
        _book(att, "testa", "T1")
        info = availability_map("testa", ["T1"], TODAY)["T1"]
        self.assertEqual(info["stato"], "libera")
        self.assertEqual(info["attivita"], [])

    def test_booking_of_another_category_is_ignored(self) -> None:
        att = _make_activity("X001", 1, "2026-09-01", "2026-09-30")
        _book(att, "mandrino", "shared")
        info = availability_map("testa", ["shared"], TODAY)["shared"]
        self.assertEqual(info["stato"], "libera")


class AvailabilityFreeWithNextJobTest(TestCase):
    """A free resource says how long it stays free and who takes it next."""

    def test_next_engagement_is_announced(self) -> None:
        att = _make_activity("X001", 1, "2026-09-21", "2026-09-30")
        _book(att, "testa", "T1")
        info = availability_map("testa", ["T1"], TODAY)["T1"]
        self.assertEqual(info["stato"], "libera")
        self.assertEqual(info["prossimo_impegno"], "2026-09-21")
        self.assertEqual(info["giorni"], 12)
        self.assertEqual([a["attivita_id"] for a in info["attivita"]], [att.pk])
        self.assertEqual(
            info["tooltip"],
            "Libera oggi.\n"
            "Impegnata fra 12 giorni, dal 21/09/2026:\n"
            f"• {att} (21/09/2026 – 30/09/2026)",
        )

    def test_tomorrow_is_spelled_out(self) -> None:
        att = _make_activity("X001", 1, "2026-09-10", "2026-09-12")
        _book(att, "testa", "T1")
        info = availability_map("testa", ["T1"], TODAY)["T1"]
        self.assertEqual(info["giorni"], 1)
        self.assertIn("Impegnata da domani (10/09/2026):", info["tooltip"])

    def test_upcoming_activities_are_sorted_and_capped(self) -> None:
        for i, (di, df) in enumerate(
            [
                ("2026-11-01", "2026-11-05"),
                ("2026-10-01", "2026-10-05"),
                ("2026-12-01", "2026-12-05"),
                ("2026-09-20", "2026-09-25"),
            ]
        ):
            _book(_make_activity(f"X00{i}", 1, di, df), "testa", "T1")
        info = availability_map("testa", ["T1"], TODAY)["T1"]
        self.assertEqual(info["prossimo_impegno"], "2026-09-20")
        self.assertEqual(
            [a["data_inizio"] for a in info["attivita"]],
            ["2026-09-20", "2026-10-01", "2026-11-01", "2026-12-01"],
        )
        self.assertEqual(info["tooltip"].count("•"), 4)
        self.assertIn("• + 1 altra attività", info["tooltip"])


class AvailabilityOccupiedTest(TestCase):
    """An occupied resource names the activity holding it and its release day."""

    def test_occupied_today(self) -> None:
        att = _make_activity("X001", 1, "2026-09-01", "2026-09-15")
        _book(att, "testa", "T1")
        info = availability_map("testa", ["T1"], TODAY)["T1"]
        self.assertEqual(info["stato"], "occupata")
        self.assertFalse(info["libera"])
        self.assertEqual(info["occupata_fino"], "2026-09-15")
        self.assertEqual(info["libera_dal"], "2026-09-16")
        self.assertEqual(info["giorni"], 7)
        self.assertEqual([a["attivita_id"] for a in info["attivita"]], [att.pk])
        self.assertEqual(
            info["tooltip"],
            "Occupata oggi da:\n"
            f"• {att} (01/09/2026 – 15/09/2026)\n"
            "Si libera fra 7 giorni (16/09/2026).",
        )

    def test_activity_label_is_not_url_encoded(self) -> None:
        att = Attivita.objects.create(
            commessa="25078",
            item="01+SNA",
            activity="qualifica",
            data_inizio=date(2026, 9, 1),
            data_fine=date(2026, 9, 15),
        )
        _book(att, "testa", "T1")
        info = availability_map("testa", ["T1"], TODAY)["T1"]
        self.assertEqual(info["attivita"][0]["label"], "25078-01+SNA-qualifica")
        self.assertIn("• 25078-01+SNA-qualifica (", info["tooltip"])
        self.assertNotIn("%2B", info["tooltip"])

    def test_booking_ending_today_still_occupies(self) -> None:
        _book(_make_activity("X001", 1, "2026-09-01", "2026-09-09"), "testa", "T1")
        info = availability_map("testa", ["T1"], TODAY)["T1"]
        self.assertEqual(info["stato"], "occupata")
        self.assertEqual(info["libera_dal"], "2026-09-10")
        self.assertIn("Si libera domani (10/09/2026).", info["tooltip"])

    def test_occupancy_follows_the_actual_dates(self) -> None:
        """A late start holds the resource past the planned end date."""
        att = _make_activity(
            "X001", 1, "2026-08-25", "2026-09-05", actual_start="2026-09-01"
        )
        _book(att, "testa", "T1")
        info = availability_map("testa", ["T1"], TODAY)["T1"]
        self.assertEqual(info["stato"], "occupata")
        self.assertEqual(info["occupata_fino"], "2026-09-12")
        self.assertEqual(info["libera_dal"], "2026-09-13")

    def test_back_to_back_bookings_extend_the_release_day(self) -> None:
        first = _make_activity("X001", 1, "2026-09-01", "2026-09-15")
        second = _make_activity("X002", 1, "2026-09-16", "2026-09-20")
        _book(first, "testa", "T1")
        _book(second, "testa", "T1")
        info = availability_map("testa", ["T1"], TODAY)["T1"]
        self.assertEqual(info["libera_dal"], "2026-09-21")
        self.assertEqual(
            [a["attivita_id"] for a in info["attivita"]], [first.pk, second.pk]
        )

    def test_a_gap_stops_the_chain(self) -> None:
        _book(_make_activity("X001", 1, "2026-09-01", "2026-09-15"), "testa", "T1")
        _book(_make_activity("X002", 1, "2026-09-18", "2026-09-20"), "testa", "T1")
        info = availability_map("testa", ["T1"], TODAY)["T1"]
        self.assertEqual(info["libera_dal"], "2026-09-16")
        self.assertEqual(len(info["attivita"]), 1)


class AvailabilityMapTest(TestCase):
    """The map answers for every requested uid in a single pass."""

    def test_each_uid_gets_its_own_state(self) -> None:
        _book(_make_activity("X001", 1, "2026-09-01", "2026-09-15"), "testa", "T1")
        result = availability_map("testa", ["T1", "T2", "T3"], TODAY)
        self.assertEqual(set(result), {"T1", "T2", "T3"})
        self.assertEqual(result["T1"]["stato"], "occupata")
        self.assertEqual(result["T2"]["stato"], "libera")
        self.assertEqual(result["T3"]["stato"], "libera")

    def test_no_uid_no_query(self) -> None:
        self.assertEqual(availability_map("testa", [], TODAY), {})


class AvailabilityCellHtmlTest(TestCase):
    """The dot markup carries the state class and the hover tooltip."""

    def test_free_dot(self) -> None:
        info = availability_map("testa", ["T1"], TODAY)["T1"]
        html = availability_cell_html(info)
        self.assertIn('class="inv-avail inv-avail--libera"', html)
        self.assertIn('data-tooltip="Libera oggi.', html)
        self.assertIn('<span class="inv-avail-dot"></span>', html)

    def test_occupied_dot(self) -> None:
        _book(_make_activity("X001", 1, "2026-09-01", "2026-09-15"), "testa", "T1")
        html = availability_cell_html(availability_map("testa", ["T1"], TODAY)["T1"])
        self.assertIn('class="inv-avail inv-avail--occupata"', html)
        self.assertIn("Si libera fra 7 giorni (16/09/2026).", html)

    def test_tooltip_is_escaped(self) -> None:
        html = availability_cell_html(
            {
                "stato": "occupata",
                "etichetta": "Occupata",
                "tooltip": 'Occupata da <img src="x">',
            }
        )
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)


class AnnotateInventoryRowsTest(TestCase):
    """Inventory rows are annotated with both the descriptor and its markup."""

    def test_rows_get_availability(self) -> None:
        _book(_make_activity("X001", 1, "2026-09-01", "2026-09-15"), "generatore", "G1")
        rows = [{"uid": "G1"}, {"uid": "G2"}]
        annotate_inventory_rows("Generatore", rows, TODAY)
        self.assertEqual(rows[0]["disponibilita"]["stato"], "occupata")
        self.assertIn("inv-avail--occupata", rows[0]["disponibilita_html"])
        self.assertEqual(rows[1]["disponibilita"]["stato"], "libera")
        self.assertIn("inv-avail--libera", rows[1]["disponibilita_html"])

    def test_unknown_table_falls_back_to_free(self) -> None:
        rows = [{"uid": "X1"}]
        annotate_inventory_rows("Sconosciuto", rows, TODAY)
        self.assertEqual(rows[0]["disponibilita"]["stato"], "libera")


class InventarioPageAvailabilityTest(TestCase):
    """The inventory page shows the column for every category."""

    def setUp(self) -> None:
        self.client = Client()
        self.client.force_login(create_reader("magazziniere"))
        GeneratoreItem.objects.create(uid="G1", model="g", plant="Valbrembo")
        GeneratoreItem.objects.create(uid="G2", model="g", plant="Valbrembo")
        TestaItem.objects.create(uid="T1", model="t", plant="Valbrembo")
        MandrinoItem.objects.create(uid="M1", diameter="12", plant="Valbrembo")
        TastatoreItem.objects.create(uid="TA1", diameter="12", plant="Valbrembo")
        MascheraItem.objects.create(uid="MS1", pitch="20", plant="Valbrembo")

    def test_column_and_dots_are_rendered(self) -> None:
        att = Attivita.objects.create(
            commessa="X001",
            item=1,
            activity="Saldatura",
            data_inizio=date.today(),
            data_fine=date.today(),
        )
        _book(att, "generatore", "G1")

        html = self.client.get(reverse("inventario")).content.decode()

        self.assertEqual(html.count(">Disponibilità</th>"), 5)
        self.assertIn("inv-avail--occupata", html)
        self.assertIn("inv-avail--libera", html)
        self.assertIn("Occupata oggi da:", html)

    def test_descriptors_are_exposed_to_the_page_script(self) -> None:
        html = self.client.get(reverse("inventario")).content.decode()
        self.assertIn("const INV_AVAILABILITY = ", html)
        self.assertIn('"Generatore":', html)
        self.assertIn('"G1":', html)
