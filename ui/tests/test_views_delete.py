"""Tests for api_attivita_delete and api_jobs_delete."""

import json

from django.test import TestCase, RequestFactory

from ui.models import Attivita, Job, RisorsaAttivita
from ui.tests.helpers import write_user
from ui.views import api_attivita_delete, api_jobs_delete


def _delete_request(factory: RequestFactory, body: dict):
    """Build a DELETE request with a JSON body, signed by a write user."""
    request = factory.delete(
        "/fake/",
        data=json.dumps(body),
        content_type="application/json",
    )
    request.user = write_user()
    return request


class AttivitaDeleteTest(TestCase):
    """api_attivita_delete removes the activity and its resources."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.act = Attivita.objects.create(commessa="A001", item=1, activity="sal")
        RisorsaAttivita.objects.create(attivita=self.act, tipo="testa", risorsa_id="T1")

    def test_returns_ok(self) -> None:
        req = _delete_request(self.factory, {"orm_id": self.act.pk})
        resp = api_attivita_delete(req)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(json.loads(resp.content)["ok"])

    def test_activity_deleted_from_db(self) -> None:
        req = _delete_request(self.factory, {"orm_id": self.act.pk})
        api_attivita_delete(req)
        self.assertFalse(Attivita.objects.filter(pk=self.act.pk).exists())

    def test_resources_deleted_with_the_activity(self) -> None:
        req = _delete_request(self.factory, {"orm_id": self.act.pk})
        api_attivita_delete(req)
        self.assertFalse(RisorsaAttivita.objects.filter(risorsa_id="T1").exists())


class AttivitaDeleteNotFoundTest(TestCase):
    """api_attivita_delete returns 404 when the activity does not exist."""

    def test_returns_404(self) -> None:
        factory = RequestFactory()
        req = _delete_request(factory, {"orm_id": 9999})
        resp = api_attivita_delete(req)
        self.assertEqual(resp.status_code, 404)


class JobsDeleteTest(TestCase):
    """api_jobs_delete removes the job and all its activities."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.job = Job.objects.create(commessa="E005", total_cost=50000.0)
        self.job_id = self.job.job_id
        for activity in ("lav", "verifica", "montaggio"):
            Attivita.objects.create(commessa="E005", item=1, activity=activity)
        self.other = Attivita.objects.create(commessa="F006", item=1, activity="lav")

    def test_job_and_activities_deleted(self) -> None:
        req = _delete_request(self.factory, {"job_id": self.job_id})
        api_jobs_delete(req)
        self.assertFalse(Job.objects.filter(job_id=self.job_id).exists())
        self.assertFalse(Attivita.objects.filter(job_id=self.job_id).exists())

    def test_other_commesse_are_untouched(self) -> None:
        req = _delete_request(self.factory, {"job_id": self.job_id})
        api_jobs_delete(req)
        self.assertTrue(Attivita.objects.filter(pk=self.other.pk).exists())

    def test_returns_404_for_unknown_job(self) -> None:
        req = _delete_request(self.factory, {"job_id": "MISSING"})
        resp = api_jobs_delete(req)
        self.assertEqual(resp.status_code, 404)

    def test_deletes_the_job_created_with_its_activities(self) -> None:
        """An activity always has a Job: deleting the commessa removes both."""
        Attivita.objects.create(commessa="MMP01", item=1, activity="sna")
        self.assertTrue(Job.objects.filter(commessa="MMP01").exists())

        req = _delete_request(self.factory, {"commessa": "MMP01"})
        resp = api_jobs_delete(req)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(json.loads(resp.content)["ok"])
        self.assertFalse(Attivita.objects.filter(commessa="MMP01").exists())
        self.assertFalse(Job.objects.filter(commessa="MMP01").exists())
