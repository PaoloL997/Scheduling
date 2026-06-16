"""Tests for runtime configuration service."""

from django.test import TestCase

from ui.models import AppSettings
from ui.services.runtime_config import load_runtime_config


class RuntimeConfigTest(TestCase):
    def test_load_runtime_config_applies_safety_bounds(self) -> None:
        obj = AppSettings.load()
        obj.heartbeat_interval_seconds = 1
        obj.heartbeat_timeout_seconds = 2
        obj.msp_dispatch_retry_interval_seconds = 0.01
        obj.msp_dispatch_retry_max_wait_seconds = 0.02
        obj.gemini_thinking_level = "invalid"
        obj.gemini_thinking_budget = 999999
        obj.save()

        cfg = load_runtime_config()
        self.assertEqual(cfg.heartbeat_interval_seconds, 5)
        self.assertEqual(cfg.heartbeat_timeout_seconds, 10)
        self.assertEqual(cfg.msp_dispatch_retry_interval_seconds, 0.1)
        self.assertEqual(cfg.msp_dispatch_retry_max_wait_seconds, 0.1)
        self.assertEqual(cfg.gemini_thinking_level, "low")
        self.assertEqual(cfg.gemini_thinking_budget, 8192)
