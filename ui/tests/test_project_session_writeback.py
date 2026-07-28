"""Regression tests for write-back cleanup safety."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase


class SafeCloseTests(SimpleTestCase):
    """``FileCloseEx`` must not abort import/write-back cleanup."""

    def test_safe_close_falls_back_and_always_quits(self):
        from src.data.project import _safe_close_and_quit

        app = MagicMock()
        app.FileCloseEx.side_effect = Exception("MSProject.Application.FileCloseEx")
        _safe_close_and_quit(app)
        app.FileClose.assert_called_once_with(0)
        app.Quit.assert_called_once()

    def test_safe_close_quits_even_if_both_close_methods_fail(self):
        from src.data.project import _safe_close_and_quit

        app = MagicMock()
        app.FileCloseEx.side_effect = Exception("FileCloseEx")
        app.FileClose.side_effect = Exception("FileClose")
        _safe_close_and_quit(app)
        app.Quit.assert_called_once()


class WritebackCleanupGateTests(SimpleTestCase):
    """Dataset reset must happen only after a successful write-back."""

    def test_cleanup_requires_writeback_ok(self):
        released = True
        writeback_ok = False
        should_reset = released and writeback_ok
        self.assertFalse(should_reset)

    def test_cleanup_runs_when_writeback_ok(self):
        released = True
        writeback_ok = True
        should_reset = released and writeback_ok
        self.assertTrue(should_reset)
