"""Centralized runtime configuration with DB-backed overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from ui.models import AppSettings


@dataclass(frozen=True)
class RuntimeConfig:
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


def load_runtime_config() -> RuntimeConfig:
    """Load runtime configuration from DB with safe fallbacks."""
    obj = AppSettings.load()

    heartbeat_interval = max(5, int(obj.heartbeat_interval_seconds or 30))
    heartbeat_timeout = max(
        heartbeat_interval * 2, int(obj.heartbeat_timeout_seconds or 90)
    )
    retry_interval = max(0.1, float(obj.msp_dispatch_retry_interval_seconds or 0.5))
    retry_max_wait = max(
        retry_interval, float(obj.msp_dispatch_retry_max_wait_seconds or 5.0)
    )

    thinking_level = (obj.gemini_thinking_level or "low").strip().lower()
    if thinking_level not in {"none", "low", "medium", "high"}:
        thinking_level = "low"

    model = (obj.gemini_model or "").strip() or "gemini-2.5-flash"
    thinking_budget = int(obj.gemini_thinking_budget or 512)
    if thinking_budget < 0:
        thinking_budget = 0
    if thinking_budget > 8192:
        thinking_budget = 8192

    return RuntimeConfig(
        mpp_path=_normalize_optional_path(obj.mpp_path, _default_mpp_path()),
        xlsx_path=_normalize_optional_path(obj.xlsx_path, _default_xlsx_path()),
        server_dir=_normalize_optional_dir(
            obj.server_dir,
            getattr(settings, "SERVER_DIR", ""),
        ),
        heartbeat_interval_seconds=heartbeat_interval,
        heartbeat_timeout_seconds=heartbeat_timeout,
        msp_dispatch_retry_interval_seconds=retry_interval,
        msp_dispatch_retry_max_wait_seconds=retry_max_wait,
        gemini_model=model,
        gemini_thinking_level=thinking_level,
        gemini_thinking_budget=thinking_budget,
        auto_import_on_startup=bool(obj.auto_import_on_startup),
    )


def gemini_api_key() -> str:
    return os.getenv("GEMINI_API_KEY", "")
