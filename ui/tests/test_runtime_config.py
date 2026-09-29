"""Tests for runtime configuration service."""

from django.test import override_settings
from django.test import TestCase

from ui.services.runtime_config import load_runtime_config


class RuntimeConfigTest(TestCase):
    @override_settings(PENALE_MAX="-3", SOLVER_TIME_LIMIT="0.2")
    def test_load_runtime_config_applies_lower_bounds(self) -> None:
        cfg = load_runtime_config()
        self.assertEqual(cfg.penale_max, 0.0)
        self.assertEqual(cfg.solver_time_limit, 1.0)

    @override_settings(PENALE_MAX="250", SOLVER_TIME_LIMIT="9000")
    def test_load_runtime_config_applies_upper_bounds(self) -> None:
        cfg = load_runtime_config()
        self.assertEqual(cfg.penale_max, 100.0)
        self.assertEqual(cfg.solver_time_limit, 600.0)

    @override_settings(OPENAI_WPS_MODEL="")
    def test_empty_wps_model_falls_back_to_default(self) -> None:
        self.assertEqual(load_runtime_config().openai_wps_model, "gpt-5.6-sol")
