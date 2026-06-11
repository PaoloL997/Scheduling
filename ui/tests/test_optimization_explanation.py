"""Tests for LLM-oriented optimization result formatting."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from src.optimization.solver import schedule

from ui.models import Job
from ui.services.optimization_problem import build_scheduling_problem
from ui.services.optimization_explanation import (
    build_result_payload_from_schedule,
    format_optimization_result_for_llm,
    format_optimization_result_for_llm_json,
    format_schedule_result_for_llm_json,
    generate_optimization_explanation,
    resolve_llm_payload,
)
from ui.views_ottimizzazione import SESSION_EXPLANATION_KEY, SESSION_RESULT_KEY
from ui.tests.test_optimization import _make_activity, _seed_inventory


def _sample_payload() -> dict:
    return {
        "job_ids": ["J1"],
        "deadlines": {"J1": "2026-09-15"},
        "metrics_before": {
            "total_penalty": 2000.0,
            "num_late_jobs": 1,
            "num_late_activities": 1,
            "n_activities": 2,
        },
        "metrics_after": {
            "total_penalty": 0.0,
            "num_late_jobs": 0,
            "num_late_activities": 0,
            "n_activities": 2,
        },
        "result": {
            "total_penalty": 0.0,
            "num_late_jobs": 0,
            "jobs": [
                {
                    "job_id": "J1",
                    "due_date": "2026-09-15",
                    "completion_date": "2026-09-10",
                    "delay_days": 0,
                    "delay_weeks": 0,
                    "penalty": 0.0,
                }
            ],
        },
        "diagnoses": [
            {
                "job_id": "J1",
                "late": False,
                "delay_days": 0,
                "primary_cause": None,
                "detail": None,
            }
        ],
        "tasks_before": [
            {
                "activity_id": "J1-01-prep",
                "job_id": "J1",
                "commessa": "J1",
                "item": "01",
                "activity": "prep",
                "plant": "Valbrembo",
                "start": "2026-06-01",
                "finish": "2026-09-20",
                "assigned_resources": ["G501"],
                "resources": "G501",
            },
            {
                "activity_id": "J1-02-work",
                "job_id": "J1",
                "commessa": "J1",
                "item": "02",
                "activity": "work",
                "plant": "Valbrembo",
                "start": "2026-06-10",
                "finish": "2026-06-15",
                "assigned_resources": ["M12"],
                "resources": "M12",
            },
        ],
        "tasks_after": [
            {
                "activity_id": "J1-01-prep",
                "job_id": "J1",
                "commessa": "J1",
                "item": "01",
                "activity": "prep",
                "plant": "Valbrembo",
                "start": "2026-06-05",
                "finish": "2026-09-10",
                "assigned_resources": ["G502"],
                "resources": "G502",
            },
            {
                "activity_id": "J1-02-work",
                "job_id": "J1",
                "commessa": "J1",
                "item": "02",
                "activity": "work",
                "plant": "Valbrembo",
                "start": "2026-06-10",
                "finish": "2026-06-15",
                "assigned_resources": ["M12"],
                "resources": "M12",
            },
        ],
    }


class OptimizationExplanationFormatTest(TestCase):
    def test_builds_compact_diff(self) -> None:
        data = format_optimization_result_for_llm(_sample_payload())

        self.assertTrue(data["meta"]["ok"])
        self.assertEqual(data["sintesi"]["penale_attuale_eur"], 2000.0)
        self.assertEqual(data["sintesi"]["penale_proposta_eur"], 0.0)
        self.assertEqual(data["sintesi"]["attivita_con_cambiamento"], 1)
        self.assertEqual(data["sintesi"]["attivita_senza_cambiamento"], 1)

        self.assertEqual(len(data["commesse"]), 1)
        self.assertTrue(data["commesse"][0]["attuale"]["in_ritardo"])
        self.assertFalse(data["commesse"][0]["proposta"]["in_ritardo"])

        self.assertEqual(len(data["attivita_modificate"]), 1)
        changed = data["attivita_modificate"][0]
        self.assertEqual(changed["activity_id"], "J1-01-prep")
        self.assertEqual(changed["attuale"]["risorse_joined"], "G501")
        self.assertEqual(changed["proposta"]["risorse_joined"], "G502")
        self.assertEqual(changed["delta"]["inizio_giorni"], 4)
        self.assertEqual(changed["delta"]["fine_giorni"], -10)
        self.assertEqual(changed["delta"]["risorse_aggiunte"], ["G502"])
        self.assertEqual(changed["delta"]["risorse_rimosse"], ["G501"])

    def test_limits_modified_activities(self) -> None:
        data = format_optimization_result_for_llm(
            _sample_payload(),
            max_attivita_modificate=0,
        )
        self.assertEqual(data["attivita_modificate"], [])
        self.assertEqual(data["attivita_invariate"]["attivita_modificate_omesse"], 1)
        self.assertEqual(data["sintesi"]["attivita_con_cambiamento"], 1)

    def test_json_wrapper_roundtrip(self) -> None:
        raw = format_optimization_result_for_llm_json(_sample_payload())
        parsed = json.loads(raw)
        self.assertTrue(parsed["meta"]["ok"])

    def test_incomplete_payload(self) -> None:
        data = format_optimization_result_for_llm({"tasks_before": []})
        self.assertFalse(data["meta"]["ok"])
        self.assertIn("errore", data["meta"])
        self.assertIn("suggerimento", data["meta"])

    def test_includes_fixed_activities_as_intoccabili(self) -> None:
        payload = _sample_payload()
        payload["fixed_activities"] = [
            {
                "activity_id": "J1-00-done",
                "job_id": "J1",
                "commessa": "J1",
                "item": "00",
                "attivita": "done",
                "stato": "in_corso",
                "inizio_effettivo": "2026-06-01",
                "fine_effettiva": None,
                "inizio_pianificato_db": "2026-06-01",
                "fine_pianificata_db": "2026-06-05",
                "risorse": "G500",
                "nota": "Intoccabile",
            }
        ]
        data = format_optimization_result_for_llm(payload)
        self.assertEqual(data["attivita_intoccabili"]["conteggio"], 1)
        self.assertEqual(
            data["attivita_intoccabili"]["attivita"][0]["activity_id"],
            "J1-00-done",
        )

    def test_resolve_rejects_inner_result_dict(self) -> None:
        with self.assertRaises(TypeError) as ctx:
            resolve_llm_payload(
                {
                    "total_penalty": 0.0,
                    "num_late_jobs": 0,
                    "jobs": [],
                }
            )
        self.assertIn("format_schedule_result_for_llm_json", str(ctx.exception))

    def test_build_payload_from_schedule(self) -> None:
        _seed_inventory()
        Job.objects.create(commessa="T001", delivery_date="2026-12-31")
        att = _make_activity("T001", 1, "solo")
        problem = build_scheduling_problem([att.job_id])
        result, diagnoses = schedule(problem, time_limit=5.0)
        self.assertIsNotNone(result)

        payload = build_result_payload_from_schedule(
            [att.job_id],
            result,
            diagnoses,
        )
        self.assertTrue(payload["tasks_before"])
        self.assertTrue(payload["tasks_after"])

        llm_json = format_schedule_result_for_llm_json(
            [att.job_id],
            result,
            diagnoses,
        )
        parsed = json.loads(llm_json)
        self.assertTrue(parsed["meta"]["ok"])


class GenerateOptimizationExplanationTest(TestCase):
    @patch("src.optimization.explain_agent.ExplainResultsAgent")
    def test_passes_raw_json_to_agent(self, agent_cls: MagicMock) -> None:
        agent_cls.return_value.run.return_value = "Spiegazione di test."
        payload = _sample_payload()

        text = generate_optimization_explanation(payload)

        self.assertEqual(text, "Spiegazione di test.")
        agent_cls.return_value.run.assert_called_once()
        llm_json = agent_cls.return_value.run.call_args[0][0]
        parsed = json.loads(llm_json)
        self.assertTrue(parsed["meta"]["ok"])
        self.assertNotIn("Analizza il seguente", llm_json)

    def test_invalid_payload_raises(self) -> None:
        with self.assertRaises(ValueError):
            generate_optimization_explanation({"tasks_before": []})


class OptimizationExplanationApiTest(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("explainuser", password="pass")
        self.client = Client()
        self.client.force_login(self.user)
        self.payload = _sample_payload()

    def test_requires_session_result(self) -> None:
        resp = self.client.post(reverse("api_ottimizzazione_spiegazione"))
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])

    @patch(
        "ui.views_ottimizzazione.generate_optimization_explanation",
        return_value="Spiegazione API.",
    )
    def test_generates_and_caches_explanation(self, mock_generate: MagicMock) -> None:
        session = self.client.session
        session[SESSION_RESULT_KEY] = self.payload
        session.save()

        resp = self.client.post(reverse("api_ottimizzazione_spiegazione"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["explanation"], "Spiegazione API.")
        self.assertFalse(data["cached"])
        mock_generate.assert_called_once_with(self.payload)

        resp2 = self.client.post(reverse("api_ottimizzazione_spiegazione"))
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertTrue(data2["cached"])
        mock_generate.assert_called_once()
        self.assertEqual(
            self.client.session.get(SESSION_EXPLANATION_KEY),
            "Spiegazione API.",
        )
