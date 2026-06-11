"""Tests for api_attivita_delete and api_jobs_delete queue reconciliation."""

import json

from django.test import TestCase, RequestFactory

from ui.models import Attivita, Job, MppSyncQueue
from ui.views import api_attivita_delete, api_jobs_delete


def _delete_request(factory: RequestFactory, body: dict):
    """Build a DELETE request with a JSON body."""
    return factory.delete(
        "/fake/",
        data=json.dumps(body),
        content_type="application/json",
    )


class AttivitaDeleteImportedTest(TestCase):
    """api_attivita_delete — activity imported from .mpp (always synced_to_mpp=True implicitly via imported)."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.act = Attivita.objects.create(
            commessa="A001",
            item=1,
            activity="sal",
            imported_from_mpp=True,
            synced_to_mpp=True,
        )

    def test_returns_ok(self) -> None:
        req = _delete_request(self.factory, {"orm_id": self.act.pk})
        resp = api_attivita_delete(req)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(json.loads(resp.content)["ok"])

    def test_activity_deleted_from_db(self) -> None:
        req = _delete_request(self.factory, {"orm_id": self.act.pk})
        api_attivita_delete(req)
        self.assertFalse(Attivita.objects.filter(pk=self.act.pk).exists())

    def test_delete_task_enqueued(self) -> None:
        req = _delete_request(self.factory, {"orm_id": self.act.pk})
        api_attivita_delete(req)
        entry = MppSyncQueue.objects.get(operation="delete_task")
        payload = json.loads(entry.payload_json)
        self.assertEqual(payload["commessa"], "A001")
        self.assertEqual(payload["item"], 1)
        self.assertEqual(payload["activity"], "sal")


class AttivitaDeleteWizardSyncedTest(TestCase):
    """api_attivita_delete — wizard activity already written to .mpp (synced_to_mpp=True)."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.act = Attivita.objects.create(
            commessa="B002",
            item=2,
            activity="qual",
            imported_from_mpp=False,
            synced_to_mpp=True,
        )

    def test_delete_task_enqueued_for_synced_wizard(self) -> None:
        req = _delete_request(self.factory, {"orm_id": self.act.pk})
        api_attivita_delete(req)
        self.assertTrue(MppSyncQueue.objects.filter(operation="delete_task").exists())


class AttivitaDeleteWizardUnsyncedTest(TestCase):
    """api_attivita_delete — wizard activity never written to .mpp (synced_to_mpp=False)."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.act = Attivita.objects.create(
            commessa="C003",
            item=3,
            activity="test",
            imported_from_mpp=False,
            synced_to_mpp=False,
        )
        # Simulate a pending add_task for this activity
        MppSyncQueue.objects.create(
            operation="add_task",
            payload_json=json.dumps(
                {
                    "attivita_id": self.act.pk,
                    "commessa": "C003",
                    "item": 3,
                    "activity": "test",
                }
            ),
        )

    def test_no_delete_task_enqueued(self) -> None:
        req = _delete_request(self.factory, {"orm_id": self.act.pk})
        api_attivita_delete(req)
        self.assertFalse(MppSyncQueue.objects.filter(operation="delete_task").exists())

    def test_pending_add_task_cancelled(self) -> None:
        req = _delete_request(self.factory, {"orm_id": self.act.pk})
        api_attivita_delete(req)
        self.assertFalse(MppSyncQueue.objects.filter(operation="add_task").exists())


class AttivitaDeleteCancelsUpdateTaskTest(TestCase):
    """api_attivita_delete cancels pending update_task regardless of sync state."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.act = Attivita.objects.create(
            commessa="D004",
            item=4,
            activity="collaudo",
            imported_from_mpp=True,
            synced_to_mpp=True,
        )
        MppSyncQueue.objects.create(
            operation="update_task",
            payload_json=json.dumps(
                {
                    "attivita_id": self.act.pk,
                    "commessa": "D004",
                    "item": 4,
                    "activity": "collaudo",
                }
            ),
        )

    def test_pending_update_task_cancelled(self) -> None:
        req = _delete_request(self.factory, {"orm_id": self.act.pk})
        api_attivita_delete(req)
        self.assertFalse(MppSyncQueue.objects.filter(operation="update_task").exists())

    def test_delete_task_still_enqueued(self) -> None:
        req = _delete_request(self.factory, {"orm_id": self.act.pk})
        api_attivita_delete(req)
        self.assertTrue(MppSyncQueue.objects.filter(operation="delete_task").exists())


class AttivitaDeleteNotFoundTest(TestCase):
    """api_attivita_delete returns 404 when the activity does not exist."""

    def test_returns_404(self) -> None:
        factory = RequestFactory()
        req = _delete_request(factory, {"orm_id": 9999})
        resp = api_attivita_delete(req)
        self.assertEqual(resp.status_code, 404)


class JobsDeleteTest(TestCase):
    """api_jobs_delete reconciles queue for all activities in the job."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.job = Job.objects.create(commessa="E005", total_cost=50000.0)
        self.job_id = self.job.job_id

        # Imported activity — always synced
        self.imported_act = Attivita.objects.create(
            commessa="E005",
            item=1,
            activity="lav",
            imported_from_mpp=True,
            synced_to_mpp=True,
        )
        # Wizard activity already synced
        self.synced_wizard_act = Attivita.objects.create(
            commessa="E005",
            item=1,
            activity="verifica",
            imported_from_mpp=False,
            synced_to_mpp=True,
        )
        # Wizard activity not yet synced — has a pending add_task
        self.unsynced_act = Attivita.objects.create(
            commessa="E005",
            item=1,
            activity="montaggio",
            imported_from_mpp=False,
            synced_to_mpp=False,
        )
        MppSyncQueue.objects.create(
            operation="add_task",
            payload_json=json.dumps(
                {
                    "attivita_id": self.unsynced_act.pk,
                    "commessa": "E005",
                    "item": 1,
                    "activity": "montaggio",
                }
            ),
        )

    def test_job_and_activities_deleted(self) -> None:
        req = _delete_request(self.factory, {"job_id": self.job_id})
        api_jobs_delete(req)
        self.assertFalse(Job.objects.filter(job_id=self.job_id).exists())
        self.assertFalse(Attivita.objects.filter(job_id=self.job_id).exists())

    def test_delete_task_enqueued_for_imported_activity(self) -> None:
        req = _delete_request(self.factory, {"job_id": self.job_id})
        api_jobs_delete(req)
        delete_entries = list(
            MppSyncQueue.objects.filter(operation="delete_task").values_list(
                "payload_json", flat=True
            )
        )
        payloads = [json.loads(p) for p in delete_entries]
        items = [p["item"] for p in payloads]
        self.assertIn(1, items)  # imported activity

    def test_delete_task_enqueued_for_synced_wizard_activity(self) -> None:
        req = _delete_request(self.factory, {"job_id": self.job_id})
        api_jobs_delete(req)
        delete_entries = list(
            MppSyncQueue.objects.filter(operation="delete_task").values_list(
                "payload_json", flat=True
            )
        )
        payloads = [json.loads(p) for p in delete_entries]
        activities = [p["activity"] for p in payloads]
        self.assertIn("verifica", activities)  # synced wizard activity

    def test_no_delete_task_for_unsynced_activity(self) -> None:
        req = _delete_request(self.factory, {"job_id": self.job_id})
        api_jobs_delete(req)
        delete_entries = list(
            MppSyncQueue.objects.filter(operation="delete_task").values_list(
                "payload_json", flat=True
            )
        )
        payloads = [json.loads(p) for p in delete_entries]
        activities = [p["activity"] for p in payloads]
        self.assertNotIn(
            "montaggio", activities
        )  # unsynced activity must NOT get delete_task

    def test_pending_add_task_cancelled_for_unsynced(self) -> None:
        req = _delete_request(self.factory, {"job_id": self.job_id})
        api_jobs_delete(req)
        self.assertFalse(MppSyncQueue.objects.filter(operation="add_task").exists())

    def test_returns_404_for_unknown_job(self) -> None:
        req = _delete_request(self.factory, {"job_id": "MISSING"})
        resp = api_jobs_delete(req)
        self.assertEqual(resp.status_code, 404)
