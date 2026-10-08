"""Parsing of request bodies, dates and amounts, shared by every view.

Measures (bores, pitches) have their own module, :mod:`ui.services.measures`,
because they carry domain rules; everything else that turns a raw request
value into a Python value lives here.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.http import HttpRequest, JsonResponse

INVALID_JSON_MESSAGE = "Body JSON non valido."


def parse_json_body(request: HttpRequest) -> dict | JsonResponse:
    """Return the JSON body of *request*, or the 400 response to send back.

    An empty body reads as ``{}``.  Callers test the result with
    ``isinstance(data, JsonResponse)`` and return it as is.
    """
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return JsonResponse({"ok": False, "error": INVALID_JSON_MESSAGE}, status=400)
    return data if isinstance(data, dict) else {}


def parse_iso_day(value: Any) -> date | None:
    """Parse ``YYYY-MM-DD`` (a longer ISO string is cut to its day) or a date."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_day_any_format(value: Any) -> date | None:
    """Parse an ISO day or the common Italian spellings (``31/12/2026`` …)."""
    parsed = parse_iso_day(value)
    if parsed is not None or value in (None, ""):
        return parsed
    text = str(value).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_decimal(value: Any) -> Decimal | None:
    """Return *value* as a :class:`~decimal.Decimal`, ``None`` when empty or invalid."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def parse_money(value: Any) -> float | None:
    """Parse an optional monetary amount such as a commessa ``total_cost``.

    Accepts plain numbers and Italian formatting (``200.000``, ``1.234,56``).

    Raises:
        ValueError: ``"invalid"`` when unreadable, ``"negative"`` when below zero.
    """
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        amount = float(value)
        if amount < 0:
            raise ValueError("negative")
        return amount
    text = str(value).strip().replace(" ", "").replace(" ", "")
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") == 1:
        left, right = text.split(".", 1)
        if len(right) == 3 and right.isdigit():
            text = left + right
    else:
        text = text.replace(".", "")
    try:
        amount = float(text)
    except ValueError as exc:
        raise ValueError("invalid") from exc
    if amount < 0:
        raise ValueError("negative")
    return amount


def parse_percent(value: Any) -> float | None:
    """Parse an optional non-negative percentage (comma or dot decimals).

    Raises:
        ValueError: ``"invalid"`` when unreadable, ``"negative"`` when below zero.
    """
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        percent = float(value)
    else:
        try:
            percent = float(str(value).strip().replace(",", "."))
        except ValueError as exc:
            raise ValueError("invalid") from exc
    if percent < 0:
        raise ValueError("negative")
    return percent
