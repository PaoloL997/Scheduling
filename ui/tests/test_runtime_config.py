"""Tests for runtime configuration service."""

from django.test import override_settings
from django.test import TestCase

from ui.services.runtime_config import load_runtime_config


class RuntimeConfigTest(TestCase):
    @override_settings(
        HEARTBEAT_INTERVAL_SECONDS="1",
        HEARTBEAT_TIMEOUT_SECONDS="2",
        MSP_DISPATCH_RETRY_INTERVAL_SECONDS="0.01",
        MSP_DISPATCH_RETRY_MAX_WAIT_SECONDS="0.02",
        GEMINI_THINKING_LEVEL="invalid",
        GEMINI_THINKING_BUDGET="999999",
    )
    def test_load_runtime_config_applies_safety_bounds(self) -> None:
        cfg = load_runtime_config()
        self.assertEqual(cfg.heartbeat_interval_seconds, 5)
        self.assertEqual(cfg.heartbeat_timeout_seconds, 10)
        self.assertEqual(cfg.msp_dispatch_retry_interval_seconds, 0.1)
        self.assertEqual(cfg.msp_dispatch_retry_max_wait_seconds, 0.1)
        self.assertEqual(cfg.gemini_thinking_level, "low")
        self.assertEqual(cfg.gemini_thinking_budget, 8192)
