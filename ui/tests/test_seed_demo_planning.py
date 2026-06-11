"""Tests for the seed_demo_planning management command."""

from datetime import date

from django.core.management import call_command
from django.test import TestCase

from ui.models import Attivita, Job, RisorsaAttivita


class SeedDemoPlanningTest(TestCase):
    def test_creates_three_commesse_with_chained_activities(self) -> None:
        call_command("seed_demo_planning", "--clear")

        self.assertEqual(
            Job.objects.filter(commessa__in=["OPT01", "OPT02", "OPT03"]).count(), 3
        )
        self.assertEqual(Attivita.objects.filter(commessa__startswith="OPT").count(), 6)

        opt01 = list(Attivita.objects.filter(commessa="OPT01").order_by("item"))
        self.assertEqual(len(opt01), 2)
        self.assertIsNone(opt01[0].predecessors)
        self.assertEqual(opt01[1].predecessors, opt01[0].activity_id)

        earliest = min(
            Attivita.objects.filter(commessa__startswith="OPT").values_list(
                "data_inizio", flat=True
            )
        )
        self.assertEqual(earliest, date(2026, 8, 10))

        opt02_start = Attivita.objects.get(commessa="OPT02", item=1).data_inizio
        opt03_start = Attivita.objects.get(commessa="OPT03", item=1).data_inizio
        self.assertEqual(opt02_start, opt03_start)

        self.assertGreater(opt02_start, opt01[1].data_fine)

        resources = RisorsaAttivita.objects.filter(
            attivita__commessa__startswith="OPT"
        ).count()
        self.assertEqual(resources, 30)
