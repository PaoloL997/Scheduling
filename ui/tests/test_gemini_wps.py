"""Tests for WPS Gemini response parsing helpers."""

from __future__ import annotations

import unittest

from ui.services.gemini import (
    extract_response_text,
    normalize_wps_gemini_payload,
    parse_wps_gemini_json,
    summarize_gemini_response,
)


class _Part:
    def __init__(self, text: str | None = None, thought: bool = False) -> None:
        self.text = text
        self.thought = thought


class _Content:
    def __init__(self, parts: list[_Part]) -> None:
        self.parts = parts


class _Candidate:
    def __init__(self, parts: list[_Part]) -> None:
        self.content = _Content(parts)


class _Response:
    def __init__(
        self, text: str | None = None, parts: list[_Part] | None = None
    ) -> None:
        self.text = text
        self.candidates = [_Candidate(parts or [])]


class GeminiWpsParsingTest(unittest.TestCase):
    def test_extract_response_text_prefers_response_text(self) -> None:
        response = _Response(text='{"diameter": 10}')
        self.assertEqual(extract_response_text(response), '{"diameter": 10}')

    def test_extract_response_text_falls_back_to_all_parts(self) -> None:
        response = _Response(
            parts=[
                _Part("thinking...", thought=True),
                _Part('{"diameter": 12, "s1": 1.2, "tube_pitch": 25}'),
            ]
        )
        text = extract_response_text(response)
        self.assertIn('"diameter": 12', text)

    def test_parse_wps_gemini_json_strips_markdown_fence(self) -> None:
        raw = '```json\n{"diameter": 20, "s1": 2, "tube_pitch": 30}\n```'
        data = parse_wps_gemini_json(raw)
        self.assertEqual(data["diameter"], 20)

    def test_normalize_wps_gemini_payload_lowercases_keys(self) -> None:
        data = normalize_wps_gemini_payload(
            {"Diameter": 25, "S1": 1.5, "Tube_Pitch": 32}
        )
        self.assertEqual(data["diameter"], 25)
        self.assertEqual(data["s1"], 1.5)
        self.assertEqual(data["tube_pitch"], 32)

    def test_summarize_gemini_response_includes_parts(self) -> None:
        response = _Response(
            parts=[
                _Part("thought", thought=True),
                _Part('{"diameter": 10, "s1": 1, "tube_pitch": 25}'),
            ]
        )
        summary = summarize_gemini_response(response)
        self.assertEqual(len(summary["candidates"]), 1)
        self.assertEqual(len(summary["candidates"][0]["parts"]), 2)
        self.assertTrue(summary["candidates"][0]["parts"][0]["thought"])
