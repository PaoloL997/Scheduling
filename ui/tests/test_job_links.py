"""Commessa and predecessors are relations, not strings that happen to match."""

from __future__ import annotations

from datetime import date

from django.db import IntegrityError
from django.test import TestCase

from ui.models import Attivita, Job, TipoAttivita
from ui.services.bridge_write import BridgeWriteError, update_attivita
from ui.services.precedences import resolve_predecessors, set_predecessors


def _att(commessa: str, item: str, activity: str) -> Attivita:
    return Attivita.objects.create(
        commessa=commessa,
        item=item,
        activity=activity,
        data_inizio=date(2026, 11, 2),
        data_fine=date(2026, 11, 6),
    )


class JobLinkTest(TestCase):
    def test_saving_an_activity_links_or_creates_its_job(self) -> None:
        att = _att("C9", "1", "A")
        self.assertEqual(att.job.commessa, "C9")
        self.assertEqual(att.job_id, Job.objects.get(commessa="C9").job_id)
        # Second activity of the same commessa reuses the row.
        _att("C9", "2", "B")
        self.assertEqual(Job.objects.filter(commessa="C9").count(), 1)
        self.assertEqual(Job.objects.get(commessa="C9").attivita.count(), 2)

    def test_existing_job_metadata_is_kept(self) -> None:
        Job.objects.create(commessa="C9", delivery_date="2026-12-31", penalty=2.5)
        att = _att("C9", "1", "A")
        self.assertEqual(att.job.delivery_date, "2026-12-31")
        self.assertEqual(att.job.penalty, 2.5)

    def test_a_job_cannot_be_renamed(self) -> None:
        job = Job.objects.create(commessa="C9")
        job.commessa = "C10"
        with self.assertRaises(ValueError):
            job.save()

    def test_moving_an_activity_relinks_it_and_clears_its_predecessors(self) -> None:
        first = _att("C9", "1", "A")
        second = _att("C9", "2", "B")
        set_predecessors(second, [first.activity_id])

        update_attivita(second.pk, {"commessa": "C10"})

        second.refresh_from_db()
        self.assertEqual(second.job.commessa, "C10")
        self.assertEqual(second.predecessor_ids(), [])
        self.assertEqual(Job.objects.get(commessa="C9").attivita.count(), 1)

    def test_a_job_with_activities_is_protected(self) -> None:
        att = _att("C9", "1", "A")
        with self.assertRaises(IntegrityError):
            att.job.delete()


class PredecessorLinkTest(TestCase):
    def test_renaming_an_activity_keeps_the_links_pointing_at_it(self) -> None:
        TipoAttivita.objects.create(nome="Taglio laser")
        first = _att("C9", "1", "Taglio")
        second = _att("C9", "2", "Saldatura")
        set_predecessors(second, [first.activity_id])
        old_id = first.activity_id

        update_attivita(first.pk, {"activity": "Taglio laser"})

        first.refresh_from_db()
        self.assertNotEqual(first.activity_id, old_id)
        self.assertEqual(second.predecessor_ids(), [first.activity_id])
        self.assertEqual(list(first.successori.all()), [second])

    def test_resolution_is_case_insensitive_and_within_the_commessa(self) -> None:
        first = _att("C9", "1", "Taglio")
        other = _att("C10", "1", "Taglio")
        self.assertEqual(
            resolve_predecessors("C9", [first.activity_id.lower()]), [first]
        )
        with self.assertRaises(ValueError):
            resolve_predecessors("C9", [other.activity_id])
        with self.assertRaises(ValueError):
            resolve_predecessors("C9", [first.activity_id], exclude_pk=first.pk)

    def test_update_refuses_an_unknown_predecessor(self) -> None:
        first = _att("C9", "1", "Taglio")
        with self.assertRaises(BridgeWriteError) as ctx:
            update_attivita(first.pk, {"predecessors": ["C9-2-Nope"]})
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rows_expose_the_links_as_ids_and_names(self) -> None:
        from ui.services.planning_read import load_task_dict

        first = _att("C9", "1", "Taglio")
        second = _att("C9", "2", "Saldatura")
        set_predecessors(second, [first.activity_id])
        task = load_task_dict(second.pk)
        self.assertEqual(task["predecessors"], first.activity_id)
        self.assertEqual(task["predecessor_names"], "Taglio")
        self.assertIsNone(load_task_dict(first.pk)["predecessors"])
