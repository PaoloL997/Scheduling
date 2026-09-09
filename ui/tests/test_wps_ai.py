"""Tests for the OpenAI-backed WPS PDF extraction service."""

from __future__ import annotations

import base64
import os
import tempfile
from unittest.mock import MagicMock, patch

import httpx
import openai
from django.test import TestCase, override_settings

from ui.services.wps_ai import (
    ReadWPS,
    WpsMeasurements,
    normalize_wps_payload,
    summarize_response,
)

PDF_BYTES = b"%PDF-1.4 fake wps content"


def _write_pdf(suffix: str = ".pdf") -> str:
    """Write a throwaway file on disk and return its path."""
    handle, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(handle, "wb") as fh:
        fh.write(PDF_BYTES)
    return path


class LoadPdfTest(TestCase):
    """The PDF is validated and base64-encoded before it leaves the server."""

    def test_missing_file_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            ReadWPS._load_pdf("C:/nope/missing.pdf")
        self.assertIn("File non trovato", str(ctx.exception))

    def test_non_pdf_is_rejected(self) -> None:
        path = _write_pdf(suffix=".txt")
        try:
            with self.assertRaises(ValueError) as ctx:
                ReadWPS._load_pdf(path)
            self.assertEqual(str(ctx.exception), "Solo file PDF sono supportati.")
        finally:
            os.remove(path)

    def test_pdf_round_trips_through_base64(self) -> None:
        path = _write_pdf()
        try:
            encoded = ReadWPS._load_pdf(path)
        finally:
            os.remove(path)
        self.assertEqual(base64.b64decode(encoded), PDF_BYTES)


class NormalizeWpsPayloadTest(TestCase):
    """Boundary guard keeping invoke()'s dict contract stable."""

    def test_keys_are_lowercased(self) -> None:
        data = normalize_wps_payload({"Diameter": 25, "S1": 1.5, "Tube_Pitch": 32})
        self.assertEqual(data, {"diameter": 25, "s1": 1.5, "tube_pitch": 32})

    def test_unknown_keys_are_dropped(self) -> None:
        data = normalize_wps_payload({"diameter": 25, "note": "irrilevante"})
        self.assertEqual(data, {"diameter": 25, "s1": None, "tube_pitch": None})

    def test_error_passes_through(self) -> None:
        self.assertEqual(
            normalize_wps_payload({"error": "boom"}),
            {"error": "boom"},
        )

    def test_non_dict_is_rejected(self) -> None:
        self.assertEqual(
            normalize_wps_payload("nope"),
            {"error": "Formato risposta AI non valido."},
        )


class WpsMeasurementsSchemaTest(TestCase):
    """The structured-output schema drives what the model may return."""

    def test_schema_declares_the_three_fields(self) -> None:
        properties = WpsMeasurements.model_json_schema()["properties"]
        self.assertEqual(set(properties), {"diameter", "s1", "tube_pitch"})


def _stub_response(measurements: WpsMeasurements | None) -> MagicMock:
    """Build a stand-in for the object returned by responses.parse."""
    response = MagicMock()
    response.output_parsed = measurements
    response.id = "resp_test"
    response.model = "gpt-5.6-sol"
    response.status = "completed"
    response.output_text = ""
    return response


@override_settings(OPENAI_API_KEY="test-key", OPENAI_WPS_MODEL="gpt-5.6-sol")
class InvokeTest(TestCase):
    """End-to-end coverage of invoke() with the OpenAI client patched out."""

    def setUp(self) -> None:
        self.pdf_path = _write_pdf()
        self.addCleanup(os.remove, self.pdf_path)

    @patch("ui.services.wps_ai.OpenAI")
    def test_successful_extraction(self, mock_openai) -> None:
        mock_openai.return_value.responses.parse.return_value = _stub_response(
            WpsMeasurements(diameter=25.0, s1=1.5, tube_pitch=32.0)
        )

        result = ReadWPS().invoke(self.pdf_path)

        self.assertEqual(result, {"diameter": 25.0, "s1": 1.5, "tube_pitch": 32.0})
        kwargs = mock_openai.return_value.responses.parse.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-5.6-sol")
        self.assertIs(kwargs["text_format"], WpsMeasurements)
        parts = kwargs["input"][0]["content"]
        self.assertEqual(parts[0]["type"], "input_file")
        self.assertTrue(
            parts[0]["file_data"].startswith("data:application/pdf;base64,")
        )

    @patch("ui.services.wps_ai.OpenAI")
    def test_empty_parse_result_is_reported(self, mock_openai) -> None:
        mock_openai.return_value.responses.parse.return_value = _stub_response(None)
        self.assertEqual(
            ReadWPS().invoke(self.pdf_path), {"error": "Risposta AI vuota."}
        )

    @patch("ui.services.wps_ai.OpenAI")
    def test_timeout_is_reported_in_italian(self, mock_openai) -> None:
        mock_openai.return_value.responses.parse.side_effect = openai.APITimeoutError(
            request=httpx.Request("POST", "https://api.openai.com/v1/responses")
        )
        result = ReadWPS().invoke(self.pdf_path)
        self.assertIn("Timeout nella risposta di OpenAI", result["error"])

    @patch("ui.services.wps_ai.OpenAI")
    def test_invalid_key_is_reported_in_italian(self, mock_openai) -> None:
        mock_openai.return_value.responses.parse.side_effect = (
            openai.AuthenticationError(
                "invalid api key",
                response=httpx.Response(
                    401,
                    request=httpx.Request(
                        "POST", "https://api.openai.com/v1/responses"
                    ),
                ),
                body=None,
            )
        )
        result = ReadWPS().invoke(self.pdf_path)
        self.assertIn("OPENAI_API_KEY non valida", result["error"])

    @patch("ui.services.wps_ai.OpenAI")
    def test_unknown_model_names_the_env_variable(self, mock_openai) -> None:
        mock_openai.return_value.responses.parse.side_effect = openai.NotFoundError(
            "model not found",
            response=httpx.Response(
                404,
                request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
            ),
            body=None,
        )
        result = ReadWPS().invoke(self.pdf_path)
        self.assertIn("OPENAI_WPS_MODEL", result["error"])


class MissingApiKeyTest(TestCase):
    """A missing key must not reach the SDK: the wizard needs a clean message."""

    @override_settings(OPENAI_API_KEY="")
    @patch("ui.services.wps_ai.OpenAI")
    def test_missing_key_short_circuits_before_the_client(self, mock_openai) -> None:
        path = _write_pdf()
        self.addCleanup(os.remove, path)

        result = ReadWPS().invoke(path)

        self.assertEqual(
            result, {"error": "OPENAI_API_KEY non configurata nel file .env."}
        )
        mock_openai.assert_not_called()


class SummarizeResponseTest(TestCase):
    """Only identifiers and counters are logged, never document content."""

    def test_summary_reports_identifiers_and_status(self) -> None:
        summary = summarize_response(
            _stub_response(WpsMeasurements(diameter=1, s1=1, tube_pitch=1))
        )
        self.assertEqual(summary["id"], "resp_test")
        self.assertEqual(summary["model"], "gpt-5.6-sol")
        self.assertEqual(summary["status"], "completed")
