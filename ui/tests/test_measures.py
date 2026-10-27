"""Tests for culture-invariant measure parsing and compatibility."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase

from ui.models import MandrinoItem, MascheraItem
from ui.services.activity_inventory import _select_n, get_available_resources
from ui.services.measures import (
    bore_in_range,
    is_resource_compatible,
    normalize_measure,
    parse_measure,
    pitch_matches,
)


class ParseMeasureTest(TestCase):
    def test_dot_and_comma_decimal_separators_read_the_same(self) -> None:
        self.assertEqual(parse_measure("12.5"), 12.5)
        self.assertEqual(parse_measure("12,5"), 12.5)
        self.assertEqual(parse_measure(" 12,5 "), 12.5)

    def test_numbers_and_decimals_pass_through(self) -> None:
        self.assertEqual(parse_measure(12), 12.0)
        self.assertEqual(parse_measure(Decimal("25.000")), 25.0)

    def test_unreadable_values_are_none_not_zero(self) -> None:
        for raw in ("abc", "", "   ", None, "12.5.3", "nan", "inf", True):
            self.assertIsNone(parse_measure(raw), raw)

    def test_zero_is_a_real_value(self) -> None:
        self.assertEqual(parse_measure("0"), 0.0)


class NormalizeMeasureTest(TestCase):
    def test_comma_becomes_dot(self) -> None:
        self.assertEqual(normalize_measure("20,5"), "20.5")
        self.assertEqual(normalize_measure(" 49,21 "), "49.21")

    def test_dot_and_integers_unchanged(self) -> None:
        self.assertEqual(normalize_measure("14.83"), "14.83")
        self.assertEqual(normalize_measure("100"), "100")
        self.assertEqual(normalize_measure(22.0), "22.0")

    def test_free_text_only_stripped(self) -> None:
        self.assertEqual(normalize_measure(" 12,5 mm circa "), "12,5 mm circa")
        self.assertEqual(normalize_measure("demo opt"), "demo opt")

    def test_empty_is_none(self) -> None:
        for raw in (None, "", "   "):
            self.assertIsNone(normalize_measure(raw))


class RangeChecksTest(TestCase):
    def test_bore_in_range_inclusive(self) -> None:
        self.assertTrue(bore_in_range("20", 20, 25))
        self.assertTrue(bore_in_range("25,0", 20, 25))
        self.assertFalse(bore_in_range("25.1", 20, 25))

    def test_unreadable_diameter_never_matches_even_a_range_with_zero(self) -> None:
        self.assertFalse(bore_in_range("abc", 0, 100))
        self.assertFalse(bore_in_range("", -1, 1))
        self.assertFalse(bore_in_range("20", None, 25))

    def test_pitch_matches_with_comma(self) -> None:
        self.assertTrue(pitch_matches("25,4", Decimal("25.4")))
        self.assertFalse(pitch_matches("25", 25.4))
        self.assertFalse(pitch_matches("x", 25.4))

    def test_is_resource_compatible_without_requirement_accepts_all(self) -> None:
        self.assertTrue(is_resource_compatible("mandrino", {"diameter": "abc"}))
        self.assertTrue(is_resource_compatible("maschera", {"pitch": ""}))
        self.assertFalse(
            is_resource_compatible(
                "mandrino", {"diameter": "abc"}, bore_min=0, bore_max=100
            )
        )


class AvailabilityUsesInvariantParsingTest(TestCase):
    def setUp(self) -> None:
        MandrinoItem.objects.create(uid="M_COMMA", diameter="12,5", plant="Valbrembo")
        MandrinoItem.objects.create(uid="M_DOT", diameter="12.7", plant="Valbrembo")
        MandrinoItem.objects.create(uid="M_TEXT", diameter="n/d", plant="Valbrembo")
        MandrinoItem.objects.create(uid="M_FAR", diameter="30", plant="Valbrembo")
        MascheraItem.objects.create(uid="MA_COMMA", pitch="25,4", plant="Valbrembo")
        MascheraItem.objects.create(uid="MA_OTHER", pitch="30", plant="Valbrembo")

    def test_comma_diameter_enters_the_bore_range(self) -> None:
        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            bore_min=12.0,
            bore_max=13.0,
            tube_pitch=25.4,
        )
        mandrini = {r["uid"] for r in result["mandrini"]}
        self.assertEqual(mandrini, {"M_COMMA", "M_DOT"})
        self.assertEqual({r["uid"] for r in result["maschere"]}, {"MA_COMMA"})

    def test_unreadable_diameter_is_excluded_from_a_range_containing_zero(self) -> None:
        result = get_available_resources(
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 5),
            bore_min=0.0,
            bore_max=100.0,
        )
        self.assertNotIn("M_TEXT", {r["uid"] for r in result["mandrini"]})

    def test_preselection_tolerates_unreadable_diameter(self) -> None:
        items = [
            {"uid": "A", "diameter": "n/d"},
            {"uid": "B", "diameter": "12,6"},
            {"uid": "C", "diameter": "20"},
        ]
        self.assertEqual(_select_n(items, 1, set(), mid=12.5), {"B"})
        self.assertEqual(_select_n(items, 2, set(), mid=12.5), {"B", "C"})
