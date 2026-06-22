"""Tests for ProjectSession idle tracking and watchdog behaviour."""

from unittest.mock import patch

from django.test import SimpleTestCase

from ui.services.project_session import ProjectSession


class ProjectSessionIdleTests(SimpleTestCase):
    def setUp(self):
        self.session = ProjectSession()
        self.session._start_hold_thread = lambda: None  # type: ignore[method-assign]

    @patch("ui.services.project_session.time.time", return_value=1000.0)
    def test_ensure_session_does_not_refresh_existing_timestamp(self, _mock_time):
        self.session.ensure_session("sess-a")
        self.session._session_last_seen["sess-a"] = 500.0

        self.session.ensure_session("sess-a")

        self.assertEqual(self.session._session_last_seen["sess-a"], 500.0)

    @patch("ui.services.project_session.time.time", return_value=1000.0)
    def test_register_session_refreshes_timestamp(self, _mock_time):
        self.session.ensure_session("sess-a")
        self.session._session_last_seen["sess-a"] = 500.0

        self.session.register_session("sess-a")

        self.assertEqual(self.session._session_last_seen["sess-a"], 1000.0)

    @patch("ui.services.project_session._heartbeat_timeout_seconds", return_value=90)
    @patch("ui.services.project_session.time.time", return_value=200.0)
    def test_expire_stale_sessions_removes_idle_sessions(self, _mock_time, _mock_timeout):
        self.session._active_sessions.add("sess-a")
        self.session._session_last_seen["sess-a"] = 100.0

        self.session._expire_stale_sessions()

        self.assertNotIn("sess-a", self.session._active_sessions)
        self.assertTrue(self.session._release_event.is_set())
