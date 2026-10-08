"""The single definitions of the resource categories and of request parsing."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase, TestCase

from ui.models import GeneratoreItem, RisorsaAttivita, TestaItem
from ui.services.parsing import (
    parse_day_any_format,
    parse_decimal,
    parse_iso_day,
    parse_json_body,
    parse_money,
    parse_percent,
)
from ui.services.resource_types import (
    CATEGORIE,
    TABLES,
    TIPI,
    TIPO_LABELS,
    model_for_table,
    model_for_tipo,
    pairs_to_risorse_dict,
    risorse_dict_to_pairs,
)


class ResourceTypesTest(TestCase):
    def test_the_three_spellings_line_up(self) -> None:
        self.assertEqual(len(TIPI), len(CATEGORIE))
        self.assertEqual(len(TIPI), len(TABLES))
        self.assertEqual(TIPO_LABELS["generatore"], "Generatore")
        self.assertEqual(
            [tipo for tipo, _label in RisorsaAttivita.TIPO_CHOICES], list(TIPI)
        )

    def test_models_by_tipo_and_table(self) -> None:
        self.assertIs(model_for_tipo("testa"), TestaItem)
        self.assertIs(model_for_table("Generatore"), GeneratoreItem)
        with self.assertRaises(KeyError):
            model_for_tipo("Testa")

    def test_pairs_round_trip(self) -> None:
        risorse = {"teste": ["T1"], "mandrini": ["M1", "", "M2"], "generatori": [7]}
        pairs = risorse_dict_to_pairs(risorse)
        self.assertEqual(
            pairs,
            [
                ("testa", "T1"),
                ("mandrino", "M1"),
                ("mandrino", "M2"),
                ("generatore", "7"),
            ],
        )
        back = pairs_to_risorse_dict(pairs)
        self.assertEqual(set(back), set(CATEGORIE))
        self.assertEqual(back["mandrini"], ["M1", "M2"])
        self.assertEqual(back["tastatori"], [])
        self.assertEqual(risorse_dict_to_pairs(None), [])


class ParsingTest(SimpleTestCase):
    def test_json_body(self) -> None:
        factory = RequestFactory()
        ok = factory.post("/x", data='{"a": 1}', content_type="application/json")
        self.assertEqual(parse_json_body(ok), {"a": 1})
        empty = factory.post("/x", data="", content_type="application/json")
        self.assertEqual(parse_json_body(empty), {})
        bad = factory.post("/x", data="{nope", content_type="application/json")
        resp = parse_json_body(bad)
        self.assertIsInstance(resp, JsonResponse)
        self.assertEqual(resp.status_code, 400)
        not_object = factory.post("/x", data="[1, 2]", content_type="application/json")
        self.assertEqual(parse_json_body(not_object), {})

    def test_days(self) -> None:
        self.assertEqual(parse_iso_day("2026-10-08T12:00:00"), date(2026, 10, 8))
        self.assertEqual(parse_iso_day(date(2026, 1, 1)), date(2026, 1, 1))
        self.assertIsNone(parse_iso_day("08/10/2026"))
        self.assertIsNone(parse_iso_day(""))
        self.assertEqual(parse_day_any_format("08/10/2026"), date(2026, 10, 8))
        self.assertEqual(parse_day_any_format("8-10-2026"), date(2026, 10, 8))
        self.assertEqual(parse_day_any_format("2026-10-08"), date(2026, 10, 8))
        self.assertIsNone(parse_day_any_format("ieri"))

    def test_numbers(self) -> None:
        self.assertEqual(parse_decimal("12.5"), Decimal("12.5"))
        self.assertIsNone(parse_decimal("abc"))
        self.assertIsNone(parse_decimal(""))
        self.assertEqual(parse_money("1.234,56"), 1234.56)
        self.assertEqual(parse_money("200.000"), 200000.0)
        with self.assertRaises(ValueError):
            parse_money("-3")
        self.assertEqual(parse_percent("2,5"), 2.5)
        self.assertIsNone(parse_percent(None))
        with self.assertRaises(ValueError):
            parse_percent("x")
