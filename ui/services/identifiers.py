"""Helpers to build and parse canonical activity and job identifiers."""

from __future__ import annotations

from urllib.parse import quote, unquote


def _enc(value: str) -> str:
    """URL-encode a string component for use in canonical identifiers.

    Args:
        value (str): Raw string to encode.

    Returns:
        str: URL-encoded string with no safe characters.
    """
    return quote((value or "").strip(), safe="")


def _dec(value: str) -> str:
    """URL-decode a string component from a canonical identifier.

    Args:
        value (str): URL-encoded string.

    Returns:
        str: Decoded string.
    """
    return unquote(value or "")


def build_job_id(commessa: str) -> str:
    """Return the canonical job identifier for a commessa.

    A job is uniquely identified by its commessa name alone; item is an
    activity-level concept and does not belong to the job record.

    Args:
        commessa (str): The commessa name.

    Returns:
        str: URL-encoded commessa name used as the job primary key.
    """
    return _enc(commessa)


def build_activity_id(commessa: str, item: int | str, activity: str) -> str:
    """Return the canonical activity identifier.

    The format is ``commessa-item-activity`` where commessa and activity are
    URL-encoded and item is zero-padded to two digits.

    Args:
        commessa (str): The commessa name.
        item (int | str): The item number within the commessa.
        activity (str): The activity name.

    Returns:
        str: Canonical activity identifier.
    """
    return f"{_enc(commessa)}-{int(item):02d}-{_enc(activity)}"


def parse_job_id(job_id: str) -> str:
    """Decode a canonical job_id back to the commessa name.

    Args:
        job_id (str): The URL-encoded commessa name used as job primary key.

    Returns:
        str: The decoded commessa name.
    """
    return _dec(job_id or "")


def parse_activity_id(activity_id: str) -> tuple[str, int, str]:
    """Parse canonical ``activity_id`` returning ``(commessa, item, activity)``.

    Args:
        activity_id (str): Canonical activity identifier in
            ``commessa-item-activity`` format.

    Returns:
        tuple[str, int, str]: Decoded ``(commessa, item, activity)`` tuple.

    Raises:
        ValueError: When the activity_id does not contain exactly three parts.
    """
    parts = (activity_id or "").split("-", 2)
    if len(parts) != 3:
        raise ValueError("activity_id non valido")
    commessa_raw, item_raw, activity_raw = parts
    return _dec(commessa_raw), int(item_raw), _dec(activity_raw)
