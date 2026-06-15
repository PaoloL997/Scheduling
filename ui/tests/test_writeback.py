"""Unit tests for :mod:`ui.services.writeback`.

All tests use :class:`unittest.mock.MagicMock` for the MS Project COM wrapper
so no COM infrastructure is required.
"""

import json
from datetime import date
from unittest.mock import MagicMock, patch

from django.test import TestCase

from ui.services.writeback import (
    _apply_add_task,
    _apply_delete_task,
    _apply_update_task,
    _find_mpp_task,
    _task_mpp_name,
    write_pending_changes,
)


class TaskMppNameTests(TestCase):
    """Tests for :func:`_task_mpp_name`."""

    def test_with_activity(self):
        """Name includes zero-padded item and activity."""
        self.assertEqual(_task_mpp_name("25033", 1, "qualifica"), "25033_01_qualifica")

    def test_without_activity(self):
        """Name omits the activity segment when empty."""
        self.assertEqual(_task_mpp_name("25033", 2, ""), "25033_02")

    def test_item_zero_padding(self):
        """Item is always zero-padded to two digits."""
        self.assertEqual(_task_mpp_name("ABC", 9, "test"), "ABC_09_test")
        self.assertEqual(_task_mpp_name("ABC", 10, "test"), "ABC_10_test")

    def test_item_as_string_coerced(self):
        """Item strings such as ``'01'`` are coerced to int before formatting."""
        self.assertEqual(_task_mpp_name("X", "1", "act"), "X_01_act")


class FindMppTaskTests(TestCase):
    """Tests for :func:`_find_mpp_task`."""

    def _mock_wrapper(self, existing_names: list[str]) -> MagicMock:
        """Return a mock project wrapper whose ``_find_task_or_none`` mimics a lookup."""
        wrapper = MagicMock()

        def _find_or_none(name):
            if name in existing_names:
                task = MagicMock()
                task.name = name
                return task
            return None

        wrapper._find_task_or_none.side_effect = _find_or_none
        return wrapper

    def test_finds_canonical_name(self):
        """Returns the COM task and canonical name when the task exists."""
        wrapper = self._mock_wrapper(["25033_01_qualifica"])
        com_task, matched = _find_mpp_task(wrapper, "25033", 1, "qualifica")
        self.assertEqual(matched, "25033_01_qualifica")
        self.assertIsNotNone(com_task)

    def test_finds_legacy_hyphen_name(self):
        """Falls back to the legacy ``-`` format when canonical name is absent."""
        wrapper = self._mock_wrapper(["25033-01-qualifica"])
        com_task, matched = _find_mpp_task(wrapper, "25033", 1, "qualifica")
        self.assertEqual(matched, "25033-01-qualifica")
        self.assertIsNotNone(com_task)

    def test_raises_when_not_found(self):
        """Raises :class:`ValueError` when the task is absent under both formats."""
        wrapper = self._mock_wrapper([])
        with self.assertRaises(ValueError):
            _find_mpp_task(wrapper, "25033", 1, "qualifica")

    def test_finds_without_activity(self):
        """Works correctly when activity is empty."""
        wrapper = self._mock_wrapper(["25033_02"])
        _, matched = _find_mpp_task(wrapper, "25033", 2, "")
        self.assertEqual(matched, "25033_02")


class ApplyAddTaskTests(TestCase):
    """Tests for :func:`_apply_add_task`."""

    def test_add_task_called_with_canonical_name(self):
        """``project_wrapper.add_task`` receives a task with the canonical name."""
        wrapper = MagicMock()
        payload = {
            "commessa": "25033",
            "item": 1,
            "activity": "saldatura",
            "data_inizio": "2025-01-01",
            "data_fine": "2025-02-01",
            "stabilimento": "Stab A",
            "responsabile": "Mario",
            "resources": [],
        }
        _apply_add_task(wrapper, payload)
        wrapper.add_task.assert_called_once()
        task_arg = wrapper.add_task.call_args[0][0]
        self.assertEqual(task_arg.name, "25033_01_saldatura")

    def test_add_task_bore_testa_fields_propagated(self):
        """Bore and testa fields are passed through to the Task passed to add_task."""
        wrapper = MagicMock()
        payload = {
            "commessa": "25033",
            "item": 1,
            "activity": "saldatura",
            "data_inizio": "2025-01-01",
            "data_fine": "2025-02-01",
            "stabilimento": "Stab A",
            "responsabile": "Mario",
            "resources": [],
            "modello_testa": "ModelX",
            "bore_min": 10.5,
            "bore_mid": 11.0,
            "bore_max": 11.5,
            "tube_pitch": 25.0,
        }
        _apply_add_task(wrapper, payload)
        task_arg = wrapper.add_task.call_args[0][0]
        self.assertEqual(task_arg.modello_testa, "ModelX")
        self.assertEqual(task_arg.bore_min, 10.5)
        self.assertEqual(task_arg.bore_mid, 11.0)
        self.assertEqual(task_arg.bore_max, 11.5)
        self.assertEqual(task_arg.tube_pitch, 25.0)

    def test_add_task_bore_testa_absent_when_not_in_payload(self):
        """bore/testa fields default to None when absent from payload."""
        wrapper = MagicMock()
        payload = {
            "commessa": "25033",
            "item": 1,
            "activity": "saldatura",
            "data_inizio": "2025-01-01",
            "data_fine": "2025-02-01",
        }
        _apply_add_task(wrapper, payload)
        task_arg = wrapper.add_task.call_args[0][0]
        self.assertIsNone(task_arg.modello_testa)
        self.assertIsNone(task_arg.bore_min)
        self.assertIsNone(task_arg.bore_mid)
        self.assertIsNone(task_arg.bore_max)
        self.assertIsNone(task_arg.tube_pitch)

    def test_synced_to_mpp_updated(self):
        """``Attivita.synced_to_mpp`` is set to True after add."""
        wrapper = MagicMock()
        payload = {
            "attivita_id": 42,
            "commessa": "X001",
            "item": 1,
            "activity": "",
            "data_inizio": "2025-01-01",
            "data_fine": "2025-06-01",
        }
        with patch("ui.models.Attivita") as MockAtt:
            _apply_add_task(wrapper, payload)
            MockAtt.objects.filter.assert_called_once_with(pk=42)
            MockAtt.objects.filter.return_value.update.assert_called_once_with(
                synced_to_mpp=True
            )


class ApplyUpdateTaskTests(TestCase):
    """Tests for :func:`_apply_update_task`."""

    def _wrapper_with(self, name: str) -> MagicMock:
        wrapper = MagicMock()
        task_mock = MagicMock()
        wrapper._find_task_or_none.side_effect = lambda n: (
            task_mock if n == name else None
        )
        return wrapper

    def test_update_called_with_correct_args(self):
        """``project_wrapper.update_task`` is called with parsed date objects."""
        wrapper = self._wrapper_with("25033_01_qualifica")
        payload = {
            "commessa": "25033",
            "item": 1,
            "activity": "qualifica",
            "start": "2025-03-01",
            "finish": "2025-04-01",
            "plant": "Stab B",
            "responsible": "Luigi",
        }
        _apply_update_task(wrapper, payload)
        wrapper.update_task.assert_called_once_with(
            "25033_01_qualifica",
            new_name=None,
            start=date(2025, 3, 1),
            end=date(2025, 4, 1),
            plant="Stab B",
            owner="Luigi",
        )

    def test_clears_actual_finish_when_payload_sets_null(self):
        """``actual_finish: null`` clears Finish1 in MS Project."""
        wrapper = self._wrapper_with("25033_01_qualifica")
        payload = {
            "commessa": "25033",
            "item": 1,
            "activity": "qualifica",
            "actual_finish": None,
        }
        _apply_update_task(wrapper, payload)
        kwargs = wrapper.update_task.call_args[1]
        self.assertIsNone(kwargs["actual_end"])
        self.assertNotIn("actual_start", kwargs)

    def test_renames_legacy_task_to_canonical(self):
        """When the task exists only under the legacy name it is renamed."""
        wrapper = self._wrapper_with("25033-01-qualifica")
        payload = {
            "commessa": "25033",
            "item": 1,
            "activity": "qualifica",
            "start": None,
            "finish": None,
            "plant": None,
            "responsible": None,
        }
        _apply_update_task(wrapper, payload)
        kwargs = wrapper.update_task.call_args[1]
        self.assertEqual(kwargs["new_name"], "25033_01_qualifica")

    def test_no_rename_when_already_canonical(self):
        """``new_name`` is None when the task already uses the canonical format."""
        wrapper = self._wrapper_with("25033_01_qualifica")
        payload = {
            "commessa": "25033",
            "item": 1,
            "activity": "qualifica",
        }
        _apply_update_task(wrapper, payload)
        kwargs = wrapper.update_task.call_args[1]
        self.assertIsNone(kwargs["new_name"])


class ApplyDeleteTaskTests(TestCase):
    """Tests for :func:`_apply_delete_task`."""

    def test_delete_called_with_found_name(self):
        """``project_wrapper.delete_task`` is called with the matched task name."""
        wrapper = MagicMock()
        wrapper._find_task_or_none.side_effect = lambda n: (
            MagicMock() if n == "25033_01" else None
        )
        payload = {"commessa": "25033", "item": 1, "activity": ""}
        _apply_delete_task(wrapper, payload)
        wrapper.delete_task.assert_called_once_with("25033_01")

    def test_delete_legacy_name(self):
        """``delete_task`` uses the legacy name when the canonical is absent."""
        wrapper = MagicMock()
        wrapper._find_task_or_none.side_effect = lambda n: (
            MagicMock() if n == "25033-01-act" else None
        )
        payload = {"commessa": "25033", "item": 1, "activity": "act"}
        _apply_delete_task(wrapper, payload)
        wrapper.delete_task.assert_called_once_with("25033-01-act")

    def test_raises_when_task_absent(self):
        """Propagates :class:`ValueError` when the task does not exist."""
        wrapper = MagicMock()
        wrapper._find_task_or_none.return_value = None
        with self.assertRaises(ValueError):
            _apply_delete_task(wrapper, {"commessa": "X", "item": 1, "activity": ""})


class WritePendingChangesTests(TestCase):
    """Tests for :func:`write_pending_changes` dispatch logic."""

    def _make_entry(self, operation: str, payload: dict):
        """Create a :class:`~ui.models.MppSyncQueue` entry in the DB."""
        from ui.models import MppSyncQueue

        return MppSyncQueue.objects.create(
            operation=operation,
            payload_json=json.dumps(payload),
        )

    def test_empty_queue_does_nothing(self):
        """No COM calls are made when the queue is empty."""
        wrapper = MagicMock()
        write_pending_changes(wrapper)
        wrapper.add_task.assert_not_called()
        wrapper.update_task.assert_not_called()
        wrapper.delete_task.assert_not_called()

    def test_add_task_entry_is_dispatched(self):
        """``add_task`` entries are forwarded to the COM wrapper."""
        from ui.models import MppSyncQueue

        self._make_entry(
            "add_task",
            {
                "commessa": "T001",
                "item": 1,
                "activity": "test",
                "data_inizio": "2025-01-01",
                "data_fine": "2025-06-01",
            },
        )
        wrapper = MagicMock()
        write_pending_changes(wrapper)
        wrapper.add_task.assert_called_once()
        self.assertEqual(MppSyncQueue.objects.count(), 0)

    def test_job_ops_are_consumed_without_error(self):
        """Job operations are deleted from the queue without calling COM methods."""
        from ui.models import MppSyncQueue

        for op in ("add_job", "update_job", "delete_job"):
            self._make_entry(op, {"commessa": "J001"})
        wrapper = MagicMock()
        write_pending_changes(wrapper)
        self.assertEqual(MppSyncQueue.objects.count(), 0)
        wrapper.add_task.assert_not_called()

    def test_failed_entry_does_not_block_remaining(self):
        """A failing entry is logged but remaining entries are still processed."""
        from ui.models import MppSyncQueue

        self._make_entry(
            "update_task", {"commessa": "MISSING", "item": 1, "activity": ""}
        )
        self._make_entry(
            "add_task",
            {
                "commessa": "T002",
                "item": 2,
                "activity": "",
                "data_inizio": "2025-01-01",
                "data_fine": "2025-03-01",
            },
        )
        wrapper = MagicMock()
        wrapper._find_task_or_none.return_value = None  # causes update_task to fail
        write_pending_changes(wrapper)
        wrapper.add_task.assert_called_once()
        self.assertEqual(MppSyncQueue.objects.count(), 0)
