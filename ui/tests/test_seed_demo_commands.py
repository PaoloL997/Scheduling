"""Tests for overlap and optimization demo seed commands."""

from datetime import date

from django.core.management import call_command
from django.test import TestCase

from ui.models import Attivita, Job, ProblemaAttivita, RisorsaAttivita
from ui.services.overlap_reassign import apply_reschedule
from ui.services.problem_detection import detect_for_all_activities


class SeedOptimizationDemoTest(TestCase):
    def test_seed_creates_jobs_and_activities(self) -> None:
        call_command("seed_optimization_demo")

        commesse = ["OPT01", "OPT02", "OPT03", "OPT04", "OPT05"]
        self.assertEqual(Job.objects.filter(commessa__in=commesse).count(), 5)
        self.assertEqual(Attivita.objects.filter(commessa__in=commesse).count(), 5)

    def test_clear_removes_jobs_not_just_activities(self) -> None:
        call_command("seed_optimization_demo")
        call_command("seed_optimization_demo", "--clear")

        commesse = ["OPT01", "OPT02", "OPT03", "OPT04", "OPT05"]
        self.assertEqual(Attivita.objects.filter(commessa__in=commesse).count(), 0)
        self.assertEqual(Job.objects.filter(commessa__in=commesse).count(), 0)

    def test_clear_removes_empty_leftover_jobs(self) -> None:
        Job.objects.create(commessa="OPT01")
        Job.objects.create(commessa="OPT02")
        call_command("seed_optimization_demo", "--clear")

        self.assertFalse(Job.objects.filter(commessa__in=["OPT01", "OPT02"]).exists())


class SeedOverlapDemoTest(TestCase):
    def test_seed_creates_jobs_and_activities(self) -> None:
        call_command("seed_overlap_demo")

        commesse = ["C101", "C205"]
        self.assertEqual(Job.objects.filter(commessa__in=commesse).count(), 2)
        self.assertEqual(Attivita.objects.filter(commessa__in=commesse).count(), 2)
        blocked = Attivita.objects.get(commessa="C205")
        self.assertEqual(
            list(
                RisorsaAttivita.objects.filter(
                    attivita=blocked, tipo="testa"
                ).values_list("risorsa_id", flat=True)
            ),
            ["OV-T2"],
        )
        note = ProblemaAttivita.objects.get(
            attivita=blocked,
            tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
            risolto=True,
        )
        self.assertIn("OV-T2", note.descrizione)

    def test_clear_removes_jobs_not_just_activities(self) -> None:
        call_command("seed_overlap_demo")
        call_command("seed_overlap_demo", "--clear")

        commesse = ["C101", "C205", "OVLAP"]
        self.assertEqual(Attivita.objects.filter(commessa__in=commesse).count(), 0)
        self.assertEqual(Job.objects.filter(commessa__in=commesse).count(), 0)

    def test_reschedule_seed_generates_proposal(self) -> None:
        call_command("seed_overlap_demo", "--reschedule")

        commesse = ["R301", "R305"]
        self.assertEqual(Job.objects.filter(commessa__in=commesse).count(), 2)
        self.assertEqual(Attivita.objects.filter(commessa="R301").count(), 3)
        self.assertEqual(Attivita.objects.filter(commessa="R305").count(), 4)
        late = Attivita.objects.get(commessa="R301", activity="Saldatura")
        blocked = Attivita.objects.get(commessa="R305", activity="Montaggio")
        collaudo = Attivita.objects.get(commessa="R305", activity="Collaudo")
        imballo = Attivita.objects.get(commessa="R305", activity="Imballo")
        docs = Attivita.objects.get(commessa="R305", activity="Documentazione")
        # Sequential in the original plan; overlap only because of the delay.
        self.assertEqual(late.data_inizio, date(2026, 8, 10))
        self.assertEqual(late.data_fine, date(2026, 8, 19))
        self.assertEqual(late.actual_start, date(2026, 8, 17))
        self.assertGreater(late.actual_start, late.data_inizio)
        self.assertEqual(blocked.data_inizio, date(2026, 8, 20))
        self.assertGreater(blocked.data_inizio, late.data_fine)
        self.assertEqual(collaudo.predecessors, blocked.activity_id)
        self.assertEqual(imballo.predecessors, collaudo.activity_id)
        self.assertEqual(docs.predecessors, "")
        # No swap alternative: the shared testa is kept and a proposal is stored.
        self.assertEqual(
            list(
                RisorsaAttivita.objects.filter(
                    attivita=blocked, tipo="testa"
                ).values_list("risorsa_id", flat=True)
            ),
            ["RS-T1"],
        )
        for att in Attivita.objects.filter(commessa__in=commesse):
            self.assertTrue(
                att.risorse.exists(),
                f"{att.activity_id} should have assigned resources",
            )
        testa_holders = set(
            RisorsaAttivita.objects.filter(
                tipo="testa", risorsa_id="RS-T1"
            ).values_list("attivita__activity", flat=True)
        )
        self.assertEqual(
            testa_holders, {"Saldatura", "Montaggio", "Collaudo", "Imballo"}
        )
        row = ProblemaAttivita.objects.filter(
            attivita=blocked,
            tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
            risolto=False,
        ).first()
        self.assertIsNotNone(row)
        proposta = row.proposta_json
        self.assertEqual(proposta.get("status"), "proposed")
        self.assertGreaterEqual(proposta.get("moved_count"), 2)
        gantt_acts = {a["activity_id"]: a for a in proposta["gantt"]["activities"]}
        self.assertIn(blocked.activity_id, gantt_acts)
        self.assertIn(collaudo.activity_id, gantt_acts)
        self.assertIn(imballo.activity_id, gantt_acts)
        self.assertIn(docs.activity_id, gantt_acts)
        self.assertIsNone(gantt_acts[docs.activity_id].get("proposed_start"))
        moved_ids = {t.get("activity_id") for t in proposta.get("tasks_after") or []}
        self.assertIn(blocked.activity_id, moved_ids)
        self.assertNotIn(docs.activity_id, moved_ids)
        blocked_res = set(proposta["blocked"]["resources"])
        self.assertIn("RS-T1", blocked_res)
        self.assertGreaterEqual(len(blocked_res), 5)

        detect_for_all_activities()
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita__commessa__in=commesse,
                tipo=ProblemaAttivita.TIPO_ACQUISTO,
                risolto=False,
            ).exists()
        )

        apply_reschedule(blocked.pk)
        collaudo.refresh_from_db()
        self.assertEqual(
            set(collaudo.risorse.values_list("tipo", flat=True)),
            {"testa", "mandrino", "tastatore", "maschera", "generatore"},
        )
        blocked.refresh_from_db()
        self.assertEqual(
            set(blocked.risorse.values_list("tipo", flat=True)),
            {"testa", "mandrino", "tastatore", "maschera", "generatore"},
        )
        detect_for_all_activities()
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita__commessa__in=commesse,
                tipo=ProblemaAttivita.TIPO_ACQUISTO,
                risolto=False,
            ).exists()
        )

    def test_reschedule_clear_removes_jobs(self) -> None:
        call_command("seed_overlap_demo", "--reschedule")
        call_command("seed_overlap_demo", "--clear")

        commesse = ["R301", "R305"]
        self.assertEqual(Attivita.objects.filter(commessa__in=commesse).count(), 0)
        self.assertEqual(Job.objects.filter(commessa__in=commesse).count(), 0)
