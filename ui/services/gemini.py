"""Gemini LLM service for extracting data from WPS PDF documents."""

from __future__ import annotations

import json
import logging
import os
import re

from google import genai
from google.genai import types

from ui.services.runtime_config import gemini_api_key, load_runtime_config

logger = logging.getLogger(__name__)

READ_WPS = """You are a precise data extraction assistant. Your task is to extract specific values from a technical table called "dimensions".

Extract the following fields:
- **Diameter**: labeled as "Ø" or "O spaccato" in the table
- **S1**: labeled as "S1" in the table
- **Tube Pitch**: labeled as "Tube Pitch" in the table

Rules:
- Return ONLY a JSON object, no explanation, no markdown, no extra text
- If a value cannot be found or is not clearly readable, set it to null
- Return ONLY the numeric value as int or float, no units, no strings
- Do not infer or guess values — only extract what is explicitly present in the table

Expected output format:
{
    "diameter": <number or null>,
    "s1": <number or null>,
    "tube_pitch": <number or null>
}"""


def _preview_text(text: str | None, limit: int = 800) -> str:
    """Return a single-line preview safe for log output."""
    if not text:
        return ""
    compact = re.sub(r"\s+", " ", str(text)).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}… [truncated, total={len(compact)} chars]"


def summarize_gemini_response(response) -> dict:
    """Build a compact, log-friendly summary of a Gemini API response."""
    summary: dict = {
        "response_text_len": len(getattr(response, "text", None) or ""),
        "response_text_preview": _preview_text(getattr(response, "text", None)),
        "candidates": [],
    }

    prompt_feedback = getattr(response, "prompt_feedback", None)
    if prompt_feedback is not None:
        summary["prompt_feedback"] = str(prompt_feedback)

    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        summary["usage_metadata"] = str(usage)

    for idx, candidate in enumerate(getattr(response, "candidates", None) or []):
        parts_info: list[dict] = []
        content = getattr(candidate, "content", None)
        for part_idx, part in enumerate(getattr(content, "parts", None) or []):
            part_text = getattr(part, "text", None) or ""
            parts_info.append(
                {
                    "part_index": part_idx,
                    "thought": getattr(part, "thought", None),
                    "text_len": len(part_text),
                    "text_preview": _preview_text(part_text),
                }
            )
        summary["candidates"].append(
            {
                "candidate_index": idx,
                "finish_reason": str(getattr(candidate, "finish_reason", None)),
                "parts": parts_info,
            }
        )
    return summary


def extract_response_text(response) -> str:
    """Collect textual model output from a Gemini generate_content response."""
    text = getattr(response, "text", None)
    if text and str(text).strip():
        return str(text).strip()

    chunks: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                chunks.append(str(part_text))

    return "\n".join(chunks).strip()


def parse_wps_gemini_json(raw_text: str) -> dict:
    """Parse a JSON object from raw Gemini text output."""
    cleaned = re.sub(r"^```[a-z]*\n?", "", raw_text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```$", "", cleaned.strip())
    if not cleaned:
        raise ValueError("Risposta Gemini vuota.")

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def normalize_wps_gemini_payload(data: dict) -> dict:
    """Normalize Gemini keys to the canonical WPS extraction schema."""
    if not isinstance(data, dict):
        return {"error": "Formato risposta Gemini non valido."}
    if data.get("error"):
        return data

    lower = {str(key).lower(): value for key, value in data.items()}
    return {
        "diameter": lower.get("diameter"),
        "s1": lower.get("s1"),
        "tube_pitch": lower.get("tube_pitch"),
    }


class ReadWPS:
    """Extract technical parameters from a WPS PDF using the Gemini API.

    Reads the PDF document and invokes Gemini to parse the dimensions table,
    returning diameter, S1 wall thickness, and tube pitch values as JSON.

    Attributes:
        THINKING_LEVEL: Reasoning depth passed to the Gemini thinking config.
        MODEL: Gemini model identifier.
        MEDIA_RESOLUTION: Media resolution setting for document processing.
    """

    THINKING_LEVEL = "low"
    MODEL = "gemini-2.5-flash"
    MEDIA_RESOLUTION = types.MediaResolution.MEDIA_RESOLUTION_HIGH

    def __init__(self) -> None:
        """Initialise the Gemini client using the API key from the environment."""
        runtime = load_runtime_config()
        self.MODEL = runtime.gemini_model
        self.THINKING_LEVEL = runtime.gemini_thinking_level
        self.client = genai.Client(api_key=gemini_api_key())
        # Structured extraction works more reliably without thinking tokens.
        self.config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            media_resolution=self.MEDIA_RESOLUTION,
        )

    @staticmethod
    def _load_pdf(path: str) -> types.Part:
        """Load a PDF file from disk and wrap it as a Gemini content Part.

        Args:
            path: Absolute path to the PDF file.

        Returns:
            types.Part: The PDF bytes wrapped as a Gemini content part.

        Raises:
            ValueError: If ``path`` does not point to an existing PDF file.
        """
        if not isinstance(path, str) or not os.path.exists(path):
            raise ValueError(f"File non trovato: {path!r}")
        if not path.lower().endswith(".pdf"):
            raise ValueError("Solo file PDF sono supportati.")
        with open(path, "rb") as fh:
            return types.Part.from_bytes(data=fh.read(), mime_type="application/pdf")

    def invoke(self, pdf_path: str) -> dict:
        """Analyse a WPS PDF and return extracted dimensional values.

        Sends the PDF to Gemini with the structured extraction prompt and
        parses the JSON response into a Python dictionary.  On any failure
        (network error, malformed JSON, etc.) returns a dict with an
        ``error`` key containing the exception message.

        Args:
            pdf_path: Absolute path to the WPS PDF document.

        Returns:
            dict: Keys ``diameter``, ``s1``, ``tube_pitch`` (numbers or None)
                on success, or ``{"error": "<message>"}`` on failure.
        """
        logger.info(
            "Gemini WPS invoke start: pdf=%s model=%s thinking_budget=%s api_key_set=%s",
            pdf_path,
            self.MODEL,
            getattr(
                getattr(self.config, "thinking_config", None),
                "thinking_budget",
                None,
            ),
            bool(gemini_api_key()),
        )
        try:
            pdf_size = os.path.getsize(pdf_path)
            logger.debug(
                "Gemini WPS PDF loaded: path=%s size_bytes=%s", pdf_path, pdf_size
            )

            pdf_part = self._load_pdf(pdf_path)
            response = self.client.models.generate_content(
                model=self.MODEL,
                contents=[pdf_part, READ_WPS],
                config=self.config,
            )

            response_summary = summarize_gemini_response(response)
            logger.info(
                "Gemini WPS raw response summary: %s",
                json.dumps(response_summary, ensure_ascii=False),
            )

            raw_text = extract_response_text(response)
            logger.info(
                "Gemini WPS extracted text: len=%s preview=%s",
                len(raw_text),
                _preview_text(raw_text),
            )

            parsed = parse_wps_gemini_json(raw_text)
            logger.info(
                "Gemini WPS parsed JSON: %s",
                json.dumps(parsed, ensure_ascii=False, default=str),
            )

            normalized = normalize_wps_gemini_payload(parsed)
            logger.info(
                "Gemini WPS normalized payload: %s",
                json.dumps(normalized, ensure_ascii=False, default=str),
            )
            return normalized
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "API_KEY_INVALID" in msg or "API key not valid" in msg.lower():
                msg = (
                    "Chiave API Gemini non valida. "
                    "Verifica GEMINI_API_KEY nel file .env (Google AI Studio)."
                )
            logger.exception("Gemini WPS invoke failed: pdf=%s error=%s", pdf_path, exc)
            return {"error": msg}
