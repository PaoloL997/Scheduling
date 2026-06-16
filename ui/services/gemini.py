"""Gemini LLM service for extracting data from WPS PDF documents."""

import os

from google import genai
from google.genai import types

from ui.services.runtime_config import gemini_api_key, load_runtime_config

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
        self.config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_budget=runtime.gemini_thinking_budget,
            ),
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
        import json
        import re

        try:
            pdf_part = self._load_pdf(pdf_path)
            response = self.client.models.generate_content(
                model=self.MODEL,
                contents=[pdf_part, READ_WPS],
                config=self.config,
            )
            raw_text = ""
            for part in response.candidates[0].content.parts:
                if not getattr(part, "thought", False):
                    raw_text += part.text

            raw_text = re.sub(
                r"^```[a-z]*\n?", "", raw_text.strip(), flags=re.MULTILINE
            )
            raw_text = re.sub(r"```$", "", raw_text.strip())
            return json.loads(raw_text.strip())
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
