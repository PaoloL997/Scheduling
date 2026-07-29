"""Utilities for parsing WPS filenames and computing bore dimensions."""

import re


def parse_wps_name(filename: str) -> dict:
    """Extract activity metadata from a WPS PDF filename.

    The expected filename format is::

        {modello_testa}_{commessa}[_{itemdisegno}].{ext}

    All separators are underscores.  ``itemdisegno`` is everything after the
    second underscore up to (but not including) the file extension; it may
    contain underscores and any characters.  If absent, item defaults to
    ``"1"``.

    For example:
    - ``HT200_AB123.pdf``                    → modello=HT200, commessa=AB123, item="1"
    - ``HT200_AB123_02.pdf``                 → modello=HT200, commessa=AB123, item="02"
    - ``HT200_AB123_Saldatura.pdf``          → modello=HT200, commessa=AB123, item="Saldatura"
    - ``HT200_25033_02_qualifica.pdf``       → modello=HT200, commessa=25033, item="02_qualifica"

    Args:
        filename: The original filename of the uploaded WPS PDF.

    Returns:
        dict: With keys ``modello_testa`` (str), ``commessa`` (str),
            ``item`` (str), and ``attivita`` (always ``""`` for compatibility).
    """
    stem = re.sub(r"\.[^.]+$", "", filename)
    parts = stem.split("_")

    modello = parts[0] if len(parts) >= 1 else ""
    commessa = parts[1] if len(parts) >= 2 else ""
    item = "_".join(parts[2:]) or "1"

    return {
        "modello_testa": modello,
        "commessa": commessa,
        "item": item,
        "attivita": "",
    }


def compute_bore(diameter, s1) -> dict:
    """Compute the internal bore diameter range from outer diameter and wall thickness.

    Applies manufacturing tolerances:
    - Diameter tolerance: ±0.15 mm when D > 25 mm, ±0.10 mm otherwise.
    - S1 tolerance: ±20 % of the nominal S1 value.

    The internal bore formula is ``D_interno = D − 2 × S1``.

    Args:
        diameter: Outer diameter in mm (int, float, or numeric string).
        s1: Wall thickness S1 in mm (int, float, or numeric string).

    Returns:
        dict: Keys ``min``, ``max``, ``mid``, ``tol_d``, ``tol_s1`` (all
            rounded to 3 decimal places), or an empty dict if either
            argument cannot be converted to a finite float.
    """
    try:
        d_val = (
            float(re.sub(r"[^\d.]", "", str(diameter)))
            if diameter not in (None, "", "null")
            else None
        )
        s1_val = (
            float(re.sub(r"[^\d.]", "", str(s1)))
            if s1 not in (None, "", "null")
            else None
        )
        if d_val is None or s1_val is None:
            return {}

        tol_d = 0.15 if d_val > 25.0 else 0.10
        tol_s1 = s1_val * 0.20

        max_val = (d_val + tol_d) - 2 * (s1_val - tol_s1)
        min_val = (d_val - tol_d) - 2 * (s1_val + tol_s1)
        mid_val = (max_val + min_val) / 2

        return {
            "min": round(min_val, 3),
            "max": round(max_val, 3),
            "mid": round(mid_val, 3),
            "tol_d": round(tol_d, 3),
            "tol_s1": round(tol_s1, 3),
        }
    except (ValueError, TypeError, ZeroDivisionError):
        return {}


def extract_tube_pitch(raw_tp) -> float | None:
    """Parse a tube pitch value from a Gemini response field.

    Args:
        raw_tp: Raw value returned by Gemini (number, string, or None).

    Returns:
        float | None: Numeric tube pitch, or None if the value is missing
            or cannot be parsed.
    """
    if raw_tp in (None, "null", ""):
        return None
    try:
        return float(re.sub(r"[^\d.]", "", str(raw_tp)))
    except (ValueError, TypeError):
        return None
