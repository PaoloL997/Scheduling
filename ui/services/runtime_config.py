"""Centralized runtime configuration loaded from environment variables."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class RuntimeConfig:
    penale_max: float
    solver_time_limit: float
    openai_wps_model: str


def _env_str(name: str, default: str) -> str:
    return str(getattr(settings, name, "")).strip() or default


def _env_float(name: str, default: float) -> float:
    raw = str(getattr(settings, name, default)).strip()
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def load_runtime_config() -> RuntimeConfig:
    """Load runtime configuration from env with safe bounds."""
    penale_max = _env_float("PENALE_MAX", 5.0)
    if penale_max < 0:
        penale_max = 0.0
    if penale_max > 100:
        penale_max = 100.0

    solver_time_limit = _env_float("SOLVER_TIME_LIMIT", 10.0)
    if solver_time_limit < 1:
        solver_time_limit = 1.0
    if solver_time_limit > 600:
        solver_time_limit = 600.0

    return RuntimeConfig(
        penale_max=penale_max,
        solver_time_limit=solver_time_limit,
        openai_wps_model=_env_str("OPENAI_WPS_MODEL", "gpt-5.6-sol"),
    )


def openai_api_key() -> str:
    """Return the OpenAI API key.

    Kept out of :class:`RuntimeConfig` on purpose: the config object is
    serialized to the app-settings endpoints, and the key must never leave
    the server.

    Returns:
        str: The configured key, or an empty string when unset.
    """
    return _env_str("OPENAI_API_KEY", "")
