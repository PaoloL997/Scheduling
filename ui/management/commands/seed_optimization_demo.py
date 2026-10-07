"""Seed demo data to exercise the optimizer: penalties and load balancing.

Two independent groups of commesse (dates are relative to today, anchored on
the optimizer's default horizon = tomorrow):

Group A - priorita/penali (testa condivisa OPT-T1, modello OPT-H1):
  - OPT01: penale alta (200k * 2.5% = 5000 EUR/settimana), 1 attivita da 5 gg
  - OPT02: penale bassa (80k * 0.5% = 400 EUR/settimana), 1 attivita da 5 gg
  Both are due after the first 5-working-day slot: only one can be on time
  because they share the single testa OPT-T1. The baseline plan puts OPT02
  first (OPT01 late); the optimizer should swap them and pay the small
  penalty instead of the large one.

Group B - bilanciamento (nessuna risorsa contesa, ampio slack):
  - OPT03 / OPT04 / OPT05: 1 attivita da 3 gg each, due date lontana.
  The baseline plan piles all three on the same days; phase D of the solver
  should spread them over the free periods instead of stacking them.

Run: ``python manage.py seed_optimization_demo``.
Use ``--clear`` to remove the demo commesse (jobs and activities) without
reseeding.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from src.optimization.calendar import WorkingCalendar

from ui.models import (
    Attivita,
    ConfigurazioneTesta,
    GeneratoreCompatibile,
    GeneratoreItem,
    Job,
    MandrinoItem,
    MascheraItem,
    RisorsaAttivita,
    Stabilimento,
    TastatoreItem,
    TestaItem,
)

_COMMESSE = ("OPT01", "OPT02", "OPT03", "OPT04", "OPT05")
_PLANT = "Valbrembo"
_HEAD_MODEL = "OPT-H1"
_GENERATOR_MODEL = "OPT-G"
_SHARED_TESTA = "OPT-T1"

# Group A: bore around 15.75, tube pitch 25.4, head OPT-H1 (single testa).
_PRIORITY_ACTIVITY = {
    "stabilimento": _PLANT,
    "responsabile": "Demo Ottimizzazione",
    "modello_testa": _HEAD_MODEL,
    "bore_min": "14.99",
    "bore_max": "16.51",
    "bore_mid": "15.75",
    "tube_pitch": "25.4",
    "duration_min": 5 * 480,  # 5 working days
    "actual_start": None,
    "actual_finish": None,
    "predecessors": "",
}

# Group B: bore around 22.0 only -> mandrino+tastatore, no testa/maschera.
_BALANCE_ACTIVITY = {
    "stabilimento": _PLANT,
    "responsabile": "Demo Ottimizzazione",
    "modello_testa": "",
    "bore_min": "21.50",
    "bore_max": "22.50",
    "bore_mid": "22.00",
    "tube_pitch": None,
    "duration_min": 3 * 480,  # 3 working days
    "actual_start": None,
    "actual_finish": None,
    "predecessors": "",
}


class Command(BaseCommand):
    """Create demo commesse for penalty-driven ordering and load balancing."""

    help = (
        "Seed 5 commesse for the optimizer demo: OPT01/OPT02 compete for the "
        "single testa OPT-T1 with very different penalties, OPT03-05 have "
        "plenty of slack and should be spread out by the load-balancing phase."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove the demo commesse and their activities, then exit.",
        )

    def handle(self, *args, **options) -> None:
        # Same anchor as the optimizer's default horizon (tomorrow): the
        # baseline dates below line up with what the solver can re-plan.
        calendar = WorkingCalendar(date.today() + timedelta(days=1))
        day = calendar.working_index_to_date

        with transaction.atomic():
            self._clear_demo()
            if options["clear"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        "Demo ottimizzazione rimossa: commesse "
                        + ", ".join(_COMMESSE)
                        + "."
                    )
                )
                return
            self._seed_reference()
            self._seed_inventory()
            self._seed_priority_group(day)
            self._seed_balance_group(day)

        self.stdout.write(
            self.style.SUCCESS(
                "Demo ottimizzazione creata: commesse " + ", ".join(_COMMESSE) + "."
            )
        )
        self.stdout.write(
            "Gruppo penali: OPT01 (5000 EUR/sett) e OPT02 (400 EUR/sett) "
            f"condividono la testa {_SHARED_TESTA}; nel piano di partenza "
            "OPT02 e' prima e OPT01 in ritardo. L'ottimizzatore deve "
            "invertirle per pagare la penale minore."
        )
        self.stdout.write(
            "Gruppo bilanciamento: OPT03/OPT04/OPT05 hanno slack ampio ma "
            "partono tutte negli stessi giorni. La fase di bilanciamento "
            "deve distribuirle nei periodi liberi."
        )
        self.stdout.write("Apri Ottimizzazione e seleziona le commesse OPT01-OPT05.")

    def _clear_demo(self) -> None:
        Attivita.objects.filter(commessa__in=_COMMESSE).delete()
        Job.objects.filter(commessa__in=_COMMESSE).delete()

    def _seed_reference(self) -> None:
        Stabilimento.objects.update_or_create(
            nome=_PLANT,
            defaults={"colore": "#16a34a"},
        )
        cfg, _ = ConfigurazioneTesta.objects.update_or_create(
            modello_testa=_HEAD_MODEL,
            defaults={
                "min_mandrini": 1,
                "min_tastatori": 1,
                "min_maschere": 1,
                "min_generatori": 1,
            },
        )
        GeneratoreCompatibile.objects.get_or_create(
            configurazione=cfg,
            modello_generatore=_GENERATOR_MODEL,
        )

    def _seed_inventory(self) -> None:
        # The only scarce resource of group A: one testa for two commesse.
        TestaItem.objects.update_or_create(
            uid=_SHARED_TESTA,
            defaults={"model": _HEAD_MODEL, "plant": _PLANT, "notes": "demo opt"},
        )
        for uid in ("OPT-C1", "OPT-C2"):
            MandrinoItem.objects.update_or_create(
                uid=uid,
                defaults={"diameter": "15.75", "plant": _PLANT, "notes": "demo opt"},
            )
        for uid in ("OPT-TA1", "OPT-TA2"):
            TastatoreItem.objects.update_or_create(
                uid=uid,
                defaults={"diameter": "15.75", "plant": _PLANT, "notes": "demo opt"},
            )
        for uid in ("OPT-M1", "OPT-M2"):
            MascheraItem.objects.update_or_create(
                uid=uid,
                defaults={"pitch": "25.4", "plant": _PLANT, "center_distance": "127"},
            )
        for uid in ("OPT-G1", "OPT-G2"):
            GeneratoreItem.objects.update_or_create(
                uid=uid,
                defaults={
                    "model": _GENERATOR_MODEL,
                    "plant": _PLANT,
                    "notes": "demo opt",
                },
            )

        # Group B: three mandrini/tastatori so the three activities never
        # compete for resources; only the load peak links them.
        for uid in ("OPT-C3", "OPT-C4", "OPT-C5"):
            MandrinoItem.objects.update_or_create(
                uid=uid,
                defaults={"diameter": "22.0", "plant": _PLANT, "notes": "demo opt"},
            )
        for uid in ("OPT-TA3", "OPT-TA4", "OPT-TA5"):
            TastatoreItem.objects.update_or_create(
                uid=uid,
                defaults={"diameter": "22.0", "plant": _PLANT, "notes": "demo opt"},
            )

    def _seed_priority_group(self, day) -> None:
        # Two 5-day slots fit before/after the due date (end of first slot):
        # whoever goes second is ~1 week late.
        due = day(5).isoformat()
        Job.objects.update_or_create(
            commessa="OPT01",
            defaults={
                "total_cost": 200_000.0,
                "penalty": 2.5,  # -> 5000 EUR/settimana
                "delivery_date": due,
            },
        )
        Job.objects.update_or_create(
            commessa="OPT02",
            defaults={
                "total_cost": 80_000.0,
                "penalty": 0.5,  # -> 400 EUR/settimana
                "delivery_date": due,
            },
        )

        # Baseline intentionally wrong: the low-penalty commessa gets the
        # first slot, so the expensive OPT01 is the one running late.
        self._upsert_activity(
            "OPT01",
            "Collaudo",
            _PRIORITY_ACTIVITY,
            start=day(5),
            finish=day(9),
        )
        self._upsert_activity(
            "OPT02",
            "Collaudo",
            _PRIORITY_ACTIVITY,
            start=day(0),
            finish=day(4),
        )

    def _seed_balance_group(self, day) -> None:
        due = day(30).isoformat()  # ~6 settimane: slack abbondante
        for commessa in ("OPT03", "OPT04", "OPT05"):
            Job.objects.update_or_create(
                commessa=commessa,
                defaults={
                    "total_cost": 50_000.0,
                    "penalty": 1.0,
                    "delivery_date": due,
                },
            )
            # Baseline: all three piled on the same first three working days.
            self._upsert_activity(
                commessa,
                "Lavorazione",
                _BALANCE_ACTIVITY,
                start=day(0),
                finish=day(2),
            )

    def _upsert_activity(
        self,
        commessa: str,
        activity: str,
        defaults: dict,
        *,
        start: date,
        finish: date,
    ) -> None:
        att, _ = Attivita.objects.update_or_create(
            commessa=commessa,
            item=1,
            activity=activity,
            defaults={
                **defaults,
                "data_inizio": start,
                "data_fine": finish,
            },
        )
        # Reset any previously applied assignment: the demo baseline has no
        # pre-assigned resources, the optimizer picks them.
        RisorsaAttivita.objects.filter(attivita=att).delete()
