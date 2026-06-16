"""Centralized runtime configuration loaded from environment variables."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


@dataclass(frozen=True)
class RuntimeConfig:
    penale_max: float
    solver_time_limit: float
    mpp_path: str
    xlsx_path: str
    server_dir: str
    heartbeat_interval_seconds: int
    heartbeat_timeout_seconds: int
    msp_dispatch_retry_interval_seconds: float
    msp_dispatch_retry_max_wait_seconds: float
    gemini_model: str
    gemini_thinking_level: str
    gemini_thinking_budget: int
    auto_import_on_startup: bool


def _default_mpp_path() -> str:
    return str(Path(settings.BASE_DIR) / "dataset.mpp")


def _default_xlsx_path() -> str:
    return str(Path(settings.BASE_DIR) / "jobs.xlsx")


def _normalize_optional_path(value: str, fallback: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raw = fallback
    path = Path(raw)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    return str(path)


def _normalize_optional_dir(value: str, fallback: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raw = fallback
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    return str(path)


def _env_str(name: str, default: str) -> str:
    return str(getattr(settings, name, "")).strip() or default


def _env_int(name: str, default: int) -> int:
    raw = str(getattr(settings, name, default)).strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = str(getattr(settings, name, default)).strip()
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = str(getattr(settings, name, default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
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

    heartbeat_interval = max(5, _env_int("HEARTBEAT_INTERVAL_SECONDS", 30))
    heartbeat_timeout = max(
        heartbeat_interval * 2, _env_int("HEARTBEAT_TIMEOUT_SECONDS", 90)
    )
    retry_interval = max(0.1, _env_float("MSP_DISPATCH_RETRY_INTERVAL_SECONDS", 0.5))
    retry_max_wait = max(
        retry_interval, _env_float("MSP_DISPATCH_RETRY_MAX_WAIT_SECONDS", 5.0)
    )

    thinking_level = _env_str("GEMINI_THINKING_LEVEL", "low").lower()
    if thinking_level not in {"none", "low", "medium", "high"}:
        thinking_level = "low"

    model = _env_str("GEMINI_MODEL", "gemini-2.5-flash")
    thinking_budget = _env_int("GEMINI_THINKING_BUDGET", 512)
    if thinking_budget < 0:
        thinking_budget = 0
    if thinking_budget > 8192:
        thinking_budget = 8192

    return RuntimeConfig(
        penale_max=penale_max,
        solver_time_limit=solver_time_limit,
        mpp_path=_normalize_optional_path(
            _env_str("MPP_PATH", ""), _default_mpp_path()
        ),
        xlsx_path=_normalize_optional_path(
            _env_str("XLSX_PATH", ""), _default_xlsx_path()
        ),
        server_dir=_normalize_optional_dir(
            _env_str("SERVER_DIR", ""),
            getattr(settings, "SERVER_DIR", ""),
        ),
        heartbeat_interval_seconds=heartbeat_interval,
        heartbeat_timeout_seconds=heartbeat_timeout,
        msp_dispatch_retry_interval_seconds=retry_interval,
        msp_dispatch_retry_max_wait_seconds=retry_max_wait,
        gemini_model=model,
        gemini_thinking_level=thinking_level,
        gemini_thinking_budget=thinking_budget,
        auto_import_on_startup=_env_bool("AUTO_IMPORT_ON_STARTUP", True),
    )


def gemini_api_key() -> str:
    return _env_str("GEMINI_API_KEY", "")
