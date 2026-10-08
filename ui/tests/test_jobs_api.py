"""Tests for job create/update API helpers."""

from __future__ import annotations

import json

from django.test import Client, TestCase

from ui.models import Attivita, Job
from ui.services.parsing import parse_money, parse_percent
from ui.tests.helpers import create_writer


class ParseJobMoneyTest(TestCase):
    def test_plain_integer(self) -> None:
        self.assertEqual(parse_money(200000), 200000.0)
        self.assertEqual(parse_money("200000"), 200000.0)

    def test_italian_thousands_separator(self) -> None:
        self.assertEqual(parse_money("200.000"), 200000.0)
        self.assertEqual(parse_money("1.234.567"), 1234567.0)

    def test_italian_decimal_comma(self) -> None:
        self.assertEqual(parse_money("1.234,56"), 1234.56)

    def test_empty_is_none(self) -> None:
        self.assertIsNone(parse_money(None))
        self.assertIsNone(parse_money(""))

    def test_negative_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_money(-1)


class ParseJobPenaltyTest(TestCase):
    def test_percent(self) -> None:
        self.assertEqual(parse_percent("1.5"), 1.5)

    def test_empty_is_none(self) -> None:
        self.assertIsNone(parse_percent(""))


class JobsUpdateApiTest(TestCase):
    def setUp(self) -> None:
        self.user = create_writer("jobapi", password="pass")
        self.client = Client()
        self.client.force_login(self.user)
        self.job = Job.objects.create(
            commessa="J200",
            total_cost=800000.0,
            penalty=1.5,
            delivery_date="2026-08-26",
        )

    def test_update_total_cost_to_200000(self) -> None:
        resp = self.client.put(
            "/api/jobs/update/",
            data=json.dumps(
                {
                    "commessa": "J200",
                    "total_cost": 200000,
                    "penalty": 1.5,
                    "delivery_date": "2026-08-26",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.job.refresh_from_db()
        self.assertEqual(self.job.total_cost, 200000.0)

    def test_update_italian_formatted_cost(self) -> None:
        resp = self.client.put(
            "/api/jobs/update/",
            data=json.dumps(
                {
                    "commessa": "J200",
                    "total_cost": "200.000",
                    "penalty": "1,5",
                    "delivery_date": "2026-08-26",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.job.refresh_from_db()
        self.assertEqual(self.job.total_cost, 200000.0)

    def test_penalty_above_max_rejected(self) -> None:
        resp = self.client.put(
            "/api/jobs/update/",
            data=json.dumps(
                {
                    "commessa": "J200",
                    "total_cost": 200000,
                    "penalty": 200000,
                    "delivery_date": "2026-08-26",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("penale", resp.json()["error"].lower())

    def test_upsert_when_job_row_missing(self) -> None:
        Attivita.objects.create(
            commessa="J201",
            item=1,
            activity="prep",
            duration_min=480,
        )
        resp = self.client.put(
            "/api/jobs/update/",
            data=json.dumps(
                {
                    "commessa": "J201",
                    "total_cost": 200000,
                    "penalty": 2,
                    "delivery_date": "2026-12-31",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        job = Job.objects.get(commessa="J201")
        self.assertEqual(job.total_cost, 200000.0)
