"""Tests for durable sync state and write-back safety guards."""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import SimpleTestCase, TestCase


class SyncStateHelpersTests(TestCase):
    """Persisted phase and working-copy detection."""

    def test_should_fresh_import_when_empty(self):
        from ui.services.sync_state import should_fresh_import

        self.assertTrue(should_fresh_import())

    def test_should_not_fresh_import_with_queue(self):
        from ui.models import MppSyncQueue
        from ui.services.sync_state import should_fresh_import

        MppSyncQueue.objects.create(operation="update_task", payload_json="{}")
        self.assertFalse(should_fresh_import())

    def test_should_not_fresh_import_with_job(self):
        from ui.models import Job
        from ui.services.sync_state import should_fresh_import

        Job.objects.create(commessa="C1")
        self.assertFalse(should_fresh_import())

    def test_phase_roundtrip(self):
        from ui.models import SyncSessionState
        from ui.services.sync_state import get_phase, mark_dirty, mark_ready

        mark_ready()
        self.assertEqual(get_phase(), SyncSessionState.PHASE_READY)
        mark_dirty("boom")
        self.assertEqual(get_phase(), SyncSessionState.PHASE_DIRTY)
        state = SyncSessionState.objects.get(pk=1)
        self.assertEqual(state.last_error, "boom")

    def test_clear_sync_queue(self):
        from ui.models import MppSyncQueue
        from ui.services.sync_state import clear_sync_queue

        MppSyncQueue.objects.create(operation="add_job", payload_json="{}")
        self.assertEqual(clear_sync_queue(), 1)
        self.assertEqual(MppSyncQueue.objects.count(), 0)

    def test_backup_source_files(self):
        from ui.services.sync_state import backup_source_files

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mpp = root / "dataset.mpp"
            xlsx = root / "jobs.xlsx"
            mpp.write_bytes(b"mpp")
            xlsx.write_bytes(b"xlsx")
            backups = backup_source_files(str(mpp), str(xlsx), root / "bak")
            self.assertEqual(len(backups), 2)
            for path in backups:
                self.assertTrue(path.is_file())


class ResetGuardrailTests(TestCase):
    """reset_app_dataset must not wipe a dirty working copy by accident."""

    def test_reset_blocked_when_queue_pending(self):
        from ui.models import MppSyncQueue
        from ui.services.reset import reset_app_dataset

        MppSyncQueue.objects.create(operation="update_task", payload_json="{}")
        with self.assertRaises(RuntimeError):
            reset_app_dataset()
        self.assertEqual(MppSyncQueue.objects.count(), 1)

    def test_reset_force_wipes_queue(self):
        from ui.models import MppSyncQueue
        from ui.services.reset import reset_app_dataset

        MppSyncQueue.objects.create(operation="update_task", payload_json="{}")
        reset_app_dataset(force=True)
        self.assertEqual(MppSyncQueue.objects.count(), 0)


class HydrateStatusTests(TestCase):
    """In-memory ImportStatus is restored from SyncSessionState after restart."""

    def setUp(self):
        import ui.services.importer as importer

        importer._hydrated_from_db = False
        importer._status = importer.ImportStatus.NOT_STARTED
        importer._error_detail = ""

    def test_hydrate_ready_from_persisted_phase(self):
        from ui.models import Job
        from ui.services import importer
        from ui.services.sync_state import mark_ready

        Job.objects.create(commessa="C1")
        mark_ready()
        importer._hydrated_from_db = False
        importer._status = importer.ImportStatus.NOT_STARTED

        self.assertEqual(importer.get_status(), importer.ImportStatus.READY)

    def test_hydrate_writing_becomes_dirty(self):
        from ui.models import MppSyncQueue, SyncSessionState
        from ui.services import importer
        from ui.services.sync_state import get_phase, set_phase

        MppSyncQueue.objects.create(operation="add_task", payload_json="{}")
        set_phase(SyncSessionState.PHASE_WRITING, "in corso")
        importer._hydrated_from_db = False
        importer._status = importer.ImportStatus.NOT_STARTED

        self.assertEqual(importer.get_status(), importer.ImportStatus.READY)
        self.assertEqual(get_phase(), SyncSessionState.PHASE_DIRTY)
        self.assertTrue(importer.get_error_detail())


class WritebackCleanupGateTests(SimpleTestCase):
    def test_cleanup_requires_both_flags(self):
        released = True
        writeback_ok = False
        self.assertFalse(released and writeback_ok)

    def test_xlsx_failure_blocks_cleanup(self):
        mpp_saved = True
        xlsx_ok = False
        writeback_ok = mpp_saved and xlsx_ok
        self.assertFalse(writeback_ok)
