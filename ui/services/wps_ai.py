"""OpenAI service for extracting data from WPS PDF documents."""

from __future__ import annotations

import base64
import json
import logging
import os
import re

import openai
from openai import OpenAI
from pydantic import BaseModel

from ui.services.runtime_config import load_runtime_config, openai_api_key

logger = logging.getLogger(__name__)

#: The upload blocks the browser while the wizard waits, so the call is kept
#: on a short leash: one retry covers a transient 429/500, no more.
REQUEST_TIMEOUT_SECONDS = 90.0
MAX_RETRIES = 1

#: Hard limit imposed by the OpenAI file input API.
MAX_PDF_BYTES = 50 * 1024 * 1024

READ_WPS = """You are a precise data extraction assistant. Your task is to extract specific values from a technical table called "dimensions".

Extract the following fields:
- **Diameter**: labeled as "Ø" or "O spaccato" in the table
- **S1**: labeled as "S1" in the table
- **Tube Pitch**: labeled as "Tube Pitch" in the table

Rules:
- If a value cannot be found or is not clearly readable, set it to null
- Return ONLY the numeric value, no units
- Do not infer or guess values — only extract what is explicitly present in the table"""


class WpsMeasurements(BaseModel):
    """Structured output schema for the WPS dimensions table."""

    diameter: float | None
    s1: float | None
    tube_pitch: float | None


def _preview_text(text: str | None, limit: int = 800) -> str:
    """Return a single-line preview safe for log output."""
    if not text:
        return ""
    compact = re.sub(r"\s+", " ", str(text)).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}… [truncated, total={len(compact)} chars]"


def summarize_response(response) -> dict:
    """Build a compact, log-friendly summary of an OpenAI response.

    Args:
        response: The object returned by ``client.responses.parse``.

    Returns:
        dict: Identifiers, status and usage counters, all stringified so the
            summary stays JSON-serializable whatever the SDK returns.
    """
    summary: dict = {
        "id": getattr(response, "id", None),
        "model": getattr(response, "model", None),
        "status": str(getattr(response, "status", None)),
    }

    usage = getattr(response, "usage", None)
    if usage is not None:
        summary["usage"] = str(usage)

    incomplete = getattr(response, "incomplete_details", None)
    if incomplete is not None:
        summary["incomplete_details"] = str(incomplete)

    refusal = getattr(response, "output_text", None)
    if refusal:
        summary["output_text_preview"] = _preview_text(refusal)

    return summary


def normalize_wps_payload(data) -> dict:
    """Normalize extracted keys to the canonical WPS schema.

    Args:
        data: Mapping returned by the model, or an error dict.

    Returns:
        dict: Keys ``diameter``, ``s1``, ``tube_pitch``, or the error dict
            unchanged when one was passed in.
    """
    if not isinstance(data, dict):
        return {"error": "Formato risposta AI non valido."}
    if data.get("error"):
        return data

    lower = {str(key).lower(): value for key, value in data.items()}
    return {
        "diameter": lower.get("diameter"),
        "s1": lower.get("s1"),
        "tube_pitch": lower.get("tube_pitch"),
    }


class ReadWPS:
    """Extract technical parameters from a WPS PDF using the OpenAI API.

    Sends the PDF to the Responses API together with the extraction prompt
    and a strict JSON schema, returning diameter, S1 wall thickness and tube
    pitch.

    Attributes:
        MODEL: OpenAI model identifier, from ``OPENAI_WPS_MODEL``.
    """

    def __init__(self) -> None:
        """Read the configured model. The client is built inside invoke()."""
        self.MODEL = load_runtime_config().openai_wps_model

    @staticmethod
    def _load_pdf(path: str) -> str:
        """Read a PDF from disk and return it base64-encoded.

        Args:
            path: Absolute path to the PDF file.

        Returns:
            str: The file contents encoded as base64 ASCII.

        Raises:
            ValueError: If ``path`` is not an existing PDF, or is too large
                for the OpenAI file input API.
        """
        if not isinstance(path, str) or not os.path.exists(path):
            raise ValueError(f"File non trovato: {path!r}")
        if not path.lower().endswith(".pdf"):
            raise ValueError("Solo file PDF sono supportati.")
        if os.path.getsize(path) > MAX_PDF_BYTES:
            raise ValueError("PDF troppo grande: massimo 50 MB.")
        with open(path, "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii")

    def invoke(self, pdf_path: str) -> dict:
        """Analyse a WPS PDF and return extracted dimensional values.

        Never raises: the wizard shows the returned message and lets the
        planner type the measurements by hand, so every failure comes back
        as an ``error`` key.

        Args:
            pdf_path: Absolute path to the WPS PDF document.

        Returns:
            dict: Keys ``diameter``, ``s1``, ``tube_pitch`` (numbers or None)
                on success, or ``{"error": "<message>"}`` on failure.
        """
        api_key = openai_api_key()
        logger.info(
            "WPS AI invoke start: pdf=%s model=%s api_key_set=%s",
            pdf_path,
            self.MODEL,
            bool(api_key),
        )
        if not api_key:
            logger.error("WPS AI invoke aborted: OPENAI_API_KEY non configurata.")
            return {"error": "OPENAI_API_KEY non configurata nel file .env."}

        try:
            b64_pdf = self._load_pdf(pdf_path)
            logger.debug(
                "WPS AI PDF loaded: path=%s size_bytes=%s",
                pdf_path,
                os.path.getsize(pdf_path),
            )

            client = OpenAI(
                api_key=api_key,
                timeout=REQUEST_TIMEOUT_SECONDS,
                max_retries=MAX_RETRIES,
            )
            response = client.responses.parse(
                model=self.MODEL,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_file",
                                "filename": os.path.basename(pdf_path),
                                "file_data": f"data:application/pdf;base64,{b64_pdf}",
                            },
                            {"type": "input_text", "text": READ_WPS},
                        ],
                    }
                ],
                text_format=WpsMeasurements,
            )

            logger.info(
                "WPS AI raw response summary: %s",
                json.dumps(summarize_response(response), ensure_ascii=False),
            )

            parsed = response.output_parsed
            if parsed is None:
                return {"error": "Risposta AI vuota."}

            normalized = normalize_wps_payload(parsed.model_dump())
            logger.info(
                "WPS AI normalized payload: %s",
                json.dumps(normalized, ensure_ascii=False, default=str),
            )
            return normalized
        except openai.AuthenticationError:
            logger.exception("WPS AI auth failed: pdf=%s", pdf_path)
            return {
                "error": "OPENAI_API_KEY non valida. "
                "Verifica la chiave nel file .env (platform.openai.com)."
            }
        except openai.RateLimitError as exc:
            logger.exception("WPS AI rate limited: pdf=%s", pdf_path)
            if "insufficient_quota" in str(exc):
                return {
                    "error": "Credito OpenAI esaurito. "
                    "Verifica il piano su platform.openai.com."
                }
            return {
                "error": "Limite di richieste OpenAI raggiunto. "
                "Riprova tra qualche minuto."
            }
        except openai.APITimeoutError:
            logger.exception("WPS AI timed out: pdf=%s", pdf_path)
            return {
                "error": "Timeout nella risposta di OpenAI. "
                "Riprova o inserisci le misure manualmente."
            }
        except openai.APIConnectionError:
            logger.exception("WPS AI connection failed: pdf=%s", pdf_path)
            return {
                "error": "Impossibile contattare OpenAI. "
                "Controlla la connessione di rete."
            }
        except openai.NotFoundError:
            logger.exception("WPS AI model not found: pdf=%s", pdf_path)
            return {
                "error": f"Modello OpenAI non disponibile ({self.MODEL}). "
                "Verifica OPENAI_WPS_MODEL nel file .env."
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("WPS AI invoke failed: pdf=%s error=%s", pdf_path, exc)
            return {"error": str(exc)}
