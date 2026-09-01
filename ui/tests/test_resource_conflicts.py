"""Tests for resource conflict detection and enforcement."""

import json
from datetime import date
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import Client, RequestFactory, TestCase

from ui.models import Attivita, Job, MppSyncQueue, RisorsaAttivita, TipoAttivita

from ui.services.resource_conflicts import (
    ResourceAssignment,
    ResourceConflictError,
    find_conflicts,
    format_conflict_message,
    occupied_resource_ids,
    occupation_window,
    validate_no_conflicts,
)
from ui.views import api_tasks_update


def _make_activity(
    commessa: str,
    item: int,
    di: str,
    df: str,
    *,
    actual_start: str | None = None,
    actual_finish: str | None = None,
) -> Attivita:
    """Create a persisted activity within a fixed date range.

    Args:
        commessa: Commessa identifier.
        item: Item number.
        di: ISO start date.
        df: ISO end date.
        actual_start: Optional ISO actual start.
        actual_finish: Optional ISO actual finish.

    Returns:
        Attivita: The created instance.
    """
    return Attivita.objects.create(
        commessa=commessa,
        item=item,
        activity="act",
        data_inizio=date.fromisoformat(di),
        data_fine=date.fromisoformat(df),
        actual_start=date.fromisoformat(actual_start) if actual_start else None,
        actual_finish=date.fromisoformat(actual_finish) if actual_finish else None,
    )


def _book(att: Attivita, tipo: str, risorsa_id: str) -> RisorsaAttivita:
    """Persist a single resource booking on an activity.

    Args:
        att: Parent activity.
        tipo: Resource category.
        risorsa_id: Resource identifier.

    Returns:
        RisorsaAttivita: The created booking row.
    """
    return RisorsaAttivita.objects.create(
        attivita=att, tipo=tipo, risorsa_id=risorsa_id
    )


class FindConflictsDisjointTest(TestCase):
    """find_conflicts returns no conflict for fully disjoint date ranges."""

    def test_no_conflict_when_ranges_do_not_overlap(self) -> None:
        a = _make_activity("X001", 1, "2026-06-01", "2026-06-10")
        _book(a, "testa", "t1")
        result = find_conflicts(
            [ResourceAssignment("testa", "t1")],
            date(2026, 6, 11),
            date(2026, 6, 20),
        )
        self.assertEqual(result, [])


class FindConflictsOverlapTest(TestCase):
    """find_conflicts detects classic overlapping ranges."""

    def test_overlap_returns_conflict_entry(self) -> None:
        a = _make_activity("X001", 1, "2026-06-01", "2026-06-10")
        _book(a, "testa", "t1")
        result = find_conflicts(
            [("testa", "t1")],
            date(2026, 6, 5),
            date(2026, 6, 15),
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tipo"], "testa")
        self.assertEqual(result[0]["risorsa_id"], "t1")
        self.assertEqual(result[0]["attivita_id"], a.pk)


class FindConflictsDifferentTypesTest(TestCase):
    """Same resource ID across different categories does not collide."""

    def test_same_id_different_type_no_conflict(self) -> None:
        a = _make_activity("X001", 1, "2026-06-01", "2026-06-10")
        _book(a, "testa", "shared")
        result = find_conflicts(
            [("mandrino", "shared")],
            date(2026, 6, 5),
            date(2026, 6, 15),
        )
        self.assertEqual(result, [])


class FindConflictsBoundaryTest(TestCase):
    """Inclusive boundary: end of A equals start of B is a conflict."""

    def test_end_equals_start_is_conflict(self) -> None:
        a = _make_activity("X001", 1, "2026-06-01", "2026-06-10")
        _book(a, "testa", "t1")
        result = find_conflicts(
            [("testa", "t1")],
            date(2026, 6, 10),
            date(2026, 6, 20),
        )
        self.assertEqual(len(result), 1)


class FindConflictsExcludeSelfTest(TestCase):
    """exclude_activity_id ignores the activity being edited."""

    def test_self_excluded(self) -> None:
        a = _make_activity("X001", 1, "2026-06-01", "2026-06-10")
        _book(a, "testa", "t1")
        result = find_conflicts(
            [("testa", "t1")],
            date(2026, 6, 5),
            date(2026, 6, 15),
            exclude_activity_id=a.pk,
        )
        self.assertEqual(result, [])


class ValidateNoConflictsTest(TestCase):
    """validate_no_conflicts raises ResourceConflictError on overlap."""

    def test_raises_with_conflict_payload(self) -> None:
        a = _make_activity("X001", 1, "2026-06-01", "2026-06-10")
        _book(a, "testa", "t1")
        with self.assertRaises(ResourceConflictError) as ctx:
            validate_no_conflicts(
                [("testa", "t1")],
                date(2026, 6, 5),
                date(2026, 6, 15),
            )
        self.assertEqual(len(ctx.exception.conflicts), 1)
        self.assertIn("t1", str(ctx.exception))
        self.assertIn("X001", str(ctx.exception))

    def test_no_raise_when_empty(self) -> None:
        validate_no_conflicts([], date(2026, 6, 5), date(2026, 6, 15))


class FormatConflictMessageTest(TestCase):
    """format_conflict_message produces descriptive Italian text."""

    def test_single_conflict_includes_resource_and_activity(self) -> None:
        a = _make_activity("X001", 1, "2026-06-01", "2026-06-10")
        _book(a, "testa", "t1")
        conflicts = find_conflicts(
            [("testa", "t1")],
            date(2026, 6, 5),
            date(2026, 6, 15),
        )
        msg = format_conflict_message(
            conflicts,
            action_hint="Usa «Modifica risorse».",
        )
        self.assertIn("la testa «t1»", msg)
        self.assertIn("01/06/2026", msg)
        self.assertIn("Usa «Modifica risorse».", msg)

    def test_multiple_conflicts_are_listed(self) -> None:
        a = _make_activity("X001", 1, "2026-06-01", "2026-06-10")
        _book(a, "testa", "t1")
        _book(a, "mandrino", "C1")
        conflicts = find_conflicts(
            [("testa", "t1"), ("mandrino", "C1")],
            date(2026, 6, 5),
            date(2026, 6, 15),
        )
        msg = format_conflict_message(conflicts)
        self.assertIn("2 risorse", msg)
        self.assertIn("Testa «t1»", msg)
        self.assertIn("Mandrino «C1»", msg)


class FindConflictsMultiAssignmentTest(TestCase):
    """Multiple candidate assignments are all checked in one call."""

    def test_multiple_conflicts_returned(self) -> None:
        a = _make_activity("X001", 1, "2026-06-01", "2026-06-10")
        _book(a, "testa", "t1")
        _book(a, "mandrino", "C1")
        result = find_conflicts(
            [("testa", "t1"), ("mandrino", "C1"), ("tastatore", "T1")],
            date(2026, 6, 5),
            date(2026, 6, 15),
        )
        self.assertEqual(len(result), 2)


class ApiTasksUpdateConflictTest(TestCase):
    """api_tasks_update rejects date changes that create resource conflicts."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.a = _make_activity("A001", 1, "2026-06-01", "2026-06-10")
        _book(self.a, "testa", "t1")
        self.b = _make_activity("B001", 1, "2026-06-20", "2026-06-25")
        _book(self.b, "testa", "t1")

    def _put(self, body: dict):
        """Build a PUT request with a JSON body."""
        return self.factory.put(
            "/fake/",
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_conflict_returns_409(self) -> None:
        req = self._put({"orm_id": self.a.pk, "finish": "2026-06-22"})
        resp = api_tasks_update(req)
        self.assertEqual(resp.status_code, 409)
        payload = json.loads(resp.content)
        self.assertFalse(payload["ok"])
        self.assertEqual(len(payload["conflicts"]), 1)
        self.assertIn("t1", payload["error"])
        self.assertIn("Modifica risorse", payload["error"])

    def test_conflict_does_not_save(self) -> None:
        req = self._put({"orm_id": self.a.pk, "finish": "2026-06-22"})
        api_tasks_update(req)
        self.a.refresh_from_db()
        self.assertEqual(self.a.data_fine.isoformat(), "2026-06-10")

    def test_conflict_does_not_enqueue(self) -> None:
        MppSyncQueue.objects.all().delete()
        req = self._put({"orm_id": self.a.pk, "finish": "2026-06-22"})
        api_tasks_update(req)
        self.assertEqual(MppSyncQueue.objects.count(), 0)

    def test_safe_date_change_succeeds(self) -> None:
        req = self._put({"orm_id": self.a.pk, "finish": "2026-06-12"})
        resp = api_tasks_update(req)
        self.assertEqual(resp.status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.data_fine.isoformat(), "2026-06-12")


class ApiSalvaAttivitaConflictTest(TestCase):
    """api_salva_attivita rejects payloads that would book occupied resources."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(username="u", password="p")
        self.client.force_login(self.user)
        TipoAttivita.objects.create(nome="act")
        self.existing = _make_activity("X001", 1, "2026-06-01", "2026-06-10")
        _book(self.existing, "testa", "t1")
        Job.objects.create(commessa="Y001")

    def _post(self, payload: dict):
        """POST a JSON body to the wizard save endpoint."""
        return self.client.post(
            "/api/attivita/salva/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_conflict_returns_409(self) -> None:
        resp = self._post(
            {
                "commessa": "Y001",
                "item": 1,
                "attivita": "act",
                "data_inizio": "2026-06-05",
                "data_fine": "2026-06-15",
                "risorse": {"teste": ["t1"]},
            }
        )
        self.assertEqual(resp.status_code, 409)
        payload = resp.json()
        self.assertEqual(len(payload["conflicts"]), 1)

    def test_conflict_does_not_create_attivita(self) -> None:
        before = Attivita.objects.count()
        self._post(
            {
                "commessa": "Y001",
                "item": 1,
                "attivita": "act",
                "data_inizio": "2026-06-05",
                "data_fine": "2026-06-15",
                "risorse": {"teste": ["t1"]},
            }
        )
        self.assertEqual(Attivita.objects.count(), before)

    def test_non_conflicting_payload_succeeds(self) -> None:
        resp = self._post(
            {
                "commessa": "Y001",
                "item": 1,
                "attivita": "act",
                "data_inizio": "2026-06-11",
                "data_fine": "2026-06-20",
                "risorse": {"teste": ["t1"]},
            }
        )
        self.assertEqual(resp.status_code, 200)


def _ns(**kwargs) -> SimpleNamespace:
    """Build a duck-typed activity for occupation_window tests."""
    fields = {
        "data_inizio": None,
        "data_fine": None,
        "actual_start": None,
        "actual_finish": None,
    }
    fields.update(kwargs)
    return SimpleNamespace(**fields)


class OccupationWindowTest(TestCase):
    """occupation_window follows execution, not the planned calendar."""

    def test_planned_only_uses_planned_range(self) -> None:
        att = _ns(
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
        )
        self.assertEqual(
            occupation_window(att),
            (date(2026, 6, 1), date(2026, 6, 10)),
        )

    def test_planned_missing_dates_returns_none(self) -> None:
        self.assertIsNone(occupation_window(_ns(data_inizio=date(2026, 6, 1))))

    def test_completed_early_ends_at_actual_finish(self) -> None:
        att = _ns(
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
            actual_start=date(2026, 6, 1),
            actual_finish=date(2026, 6, 5),
        )
        self.assertEqual(
            occupation_window(att),
            (date(2026, 6, 1), date(2026, 6, 5)),
        )

    def test_completed_uses_actual_start_when_present(self) -> None:
        att = _ns(
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
            actual_start=date(2026, 6, 3),
            actual_finish=date(2026, 6, 5),
        )
        self.assertEqual(
            occupation_window(att),
            (date(2026, 6, 3), date(2026, 6, 5)),
        )

    def test_in_progress_on_time_keeps_planned_end(self) -> None:
        att = _ns(
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
            actual_start=date(2026, 6, 1),
        )
        self.assertEqual(
            occupation_window(att),
            (date(2026, 6, 1), date(2026, 6, 10)),
        )

    def test_in_progress_late_extends_to_effective_finish(self) -> None:
        att = _ns(
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
            actual_start=date(2026, 6, 5),
        )
        self.assertEqual(
            occupation_window(att),
            (date(2026, 6, 5), date(2026, 6, 14)),
        )

    def test_in_progress_without_planned_end_occupies_one_day(self) -> None:
        att = _ns(actual_start=date(2026, 6, 5))
        self.assertEqual(
            occupation_window(att),
            (date(2026, 6, 5), date(2026, 6, 5)),
        )


class FindConflictsEarlyFinishTest(TestCase):
    """Completed-early activities free resources after actual_finish."""

    def test_no_conflict_after_actual_finish(self) -> None:
        a = _make_activity(
            "X001",
            1,
            "2026-06-01",
            "2026-06-10",
            actual_start="2026-06-01",
            actual_finish="2026-06-05",
        )
        _book(a, "testa", "t1")
        result = find_conflicts(
            [("testa", "t1")],
            date(2026, 6, 6),
            date(2026, 6, 15),
        )
        self.assertEqual(result, [])

    def test_conflict_inside_actual_window(self) -> None:
        a = _make_activity(
            "X001",
            1,
            "2026-06-01",
            "2026-06-10",
            actual_start="2026-06-01",
            actual_finish="2026-06-05",
        )
        _book(a, "testa", "t1")
        result = find_conflicts(
            [("testa", "t1")],
            date(2026, 6, 3),
            date(2026, 6, 7),
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["data_inizio"], "2026-06-01")
        self.assertEqual(result[0]["data_fine"], "2026-06-05")

    def test_inclusive_boundary_on_actual_finish(self) -> None:
        a = _make_activity(
            "X001",
            1,
            "2026-06-01",
            "2026-06-10",
            actual_start="2026-06-01",
            actual_finish="2026-06-05",
        )
        _book(a, "testa", "t1")
        result = find_conflicts(
            [("testa", "t1")],
            date(2026, 6, 5),
            date(2026, 6, 12),
        )
        self.assertEqual(len(result), 1)

    def test_occupied_ids_free_after_actual_finish(self) -> None:
        a = _make_activity(
            "X001",
            1,
            "2026-06-01",
            "2026-06-10",
            actual_start="2026-06-01",
            actual_finish="2026-06-05",
        )
        _book(a, "testa", "t1")
        self.assertEqual(
            occupied_resource_ids("testa", date(2026, 6, 6), date(2026, 6, 15)),
            set(),
        )
        self.assertEqual(
            occupied_resource_ids("testa", date(2026, 6, 1), date(2026, 6, 5)),
            {"t1"},
        )

    def test_conflict_message_uses_occupancy_dates(self) -> None:
        a = _make_activity(
            "X001",
            1,
            "2026-06-01",
            "2026-06-10",
            actual_start="2026-06-01",
            actual_finish="2026-06-05",
        )
        _book(a, "testa", "t1")
        conflicts = find_conflicts(
            [("testa", "t1")],
            date(2026, 6, 3),
            date(2026, 6, 7),
        )
        msg = format_conflict_message(conflicts)
        self.assertIn("01/06/2026", msg)
        self.assertIn("05/06/2026", msg)
        self.assertNotIn("10/06/2026", msg)


class FindConflictsInProgressDelayTest(TestCase):
    """In-progress late starts occupy through effective_finish."""

    def test_conflict_after_planned_end_while_delayed(self) -> None:
        a = _make_activity(
            "X001",
            1,
            "2026-06-01",
            "2026-06-10",
            actual_start="2026-06-05",
        )
        _book(a, "testa", "t1")
        result = find_conflicts(
            [("testa", "t1")],
            date(2026, 6, 12),
            date(2026, 6, 15),
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["data_inizio"], "2026-06-05")
        self.assertEqual(result[0]["data_fine"], "2026-06-14")

    def test_no_conflict_after_effective_finish(self) -> None:
        a = _make_activity(
            "X001",
            1,
            "2026-06-01",
            "2026-06-10",
            actual_start="2026-06-05",
        )
        _book(a, "testa", "t1")
        result = find_conflicts(
            [("testa", "t1")],
            date(2026, 6, 15),
            date(2026, 6, 20),
        )
        self.assertEqual(result, [])

    def test_gap_before_late_start_is_free(self) -> None:
        a = _make_activity(
            "X001",
            1,
            "2026-06-01",
            "2026-06-10",
            actual_start="2026-06-05",
        )
        _book(a, "testa", "t1")
        result = find_conflicts(
            [("testa", "t1")],
            date(2026, 6, 1),
            date(2026, 6, 4),
        )
        self.assertEqual(result, [])
