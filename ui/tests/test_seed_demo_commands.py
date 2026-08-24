"""Tests for overlap and optimization demo seed commands."""

from django.core.management import call_command
from django.test import TestCase

from ui.models import Attivita, Job


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

    def test_clear_removes_jobs_not_just_activities(self) -> None:
        call_command("seed_overlap_demo")
        call_command("seed_overlap_demo", "--clear")

        commesse = ["C101", "C205", "OVLAP"]
        self.assertEqual(Attivita.objects.filter(commessa__in=commesse).count(), 0)
        self.assertEqual(Job.objects.filter(commessa__in=commesse).count(), 0)
