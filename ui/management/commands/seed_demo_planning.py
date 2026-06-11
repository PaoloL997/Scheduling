"""Seed a deterministic 3-commessa planning scenario for optimization experiments.

Timeline (working-day windows):
  - ~8 week gap at the start (room for one full commessa)
  - OPT01 alone immediately after the gap
  - OPT02 and OPT03 overlapping in parallel after OPT01

Run: ``python manage.py seed_demo_planning``
"""

from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

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

_COMMESSE = ("OPT01", "OPT02", "OPT03")

# Shared activity attributes (TP60 @ Valbrembo, 5 working days each).
_ACTIVITY_DEFAULTS = {
    "stabilimento": "Valbrembo",
    "responsabile": "Demo Planner",
    "modello_testa": "TP60",
    "bore_min": "14.99",
    "bore_max": "16.51",
    "bore_mid": "15.75",
    "tube_pitch": "25.4",
    "duration_min": 2400,
    "imported_from_mpp": False,
    "synced_to_mpp": False,
}

# (commessa, item, activity_name, start, end, predecessor_item or None)
_ACTIVITY_PLAN: list[tuple[str, int, str, date, date, int | None]] = [
    ("OPT01", 1, "Preparazione", date(2026, 8, 10), date(2026, 8, 14), None),
    ("OPT01", 2, "Esecuzione", date(2026, 8, 17), date(2026, 8, 21), 1),
    ("OPT02", 1, "Preparazione", date(2026, 8, 24), date(2026, 8, 28), None),
    ("OPT02", 2, "Esecuzione", date(2026, 8, 31), date(2026, 9, 4), 1),
    ("OPT03", 1, "Preparazione", date(2026, 8, 24), date(2026, 8, 28), None),
    ("OPT03", 2, "Esecuzione", date(2026, 8, 31), date(2026, 9, 4), 1),
]

_JOB_DEFAULTS = {
    "OPT01": {"total_cost": 500_000.0, "penalty": 2.0, "delivery_date": "2026-09-15"},
    "OPT02": {"total_cost": 420_000.0, "penalty": 2.5, "delivery_date": "2026-09-30"},
    "OPT03": {"total_cost": 380_000.0, "penalty": 2.5, "delivery_date": "2026-09-30"},
}

# Per (commessa, item): resource ids by tipo (plain uid, not prefixed).
_RESOURCE_PLAN: dict[tuple[str, int], dict[str, list[str]]] = {
    ("OPT01", 1): {
        "mandrino": ["D101"],
        "tastatore": ["D201"],
        "maschera": ["D301"],
        "testa": ["D401"],
        "generatore": ["D501"],
    },
    ("OPT01", 2): {
        "mandrino": ["D102"],
        "tastatore": ["D202"],
        "maschera": ["D302"],
        "testa": ["D402"],
        "generatore": ["D502"],
    },
    ("OPT02", 1): {
        "mandrino": ["D103"],
        "tastatore": ["D203"],
        "maschera": ["D303"],
        "testa": ["D403"],
        "generatore": ["D503"],
    },
    ("OPT02", 2): {
        "mandrino": ["D104"],
        "tastatore": ["D204"],
        "maschera": ["D304"],
        "testa": ["D404"],
        "generatore": ["D504"],
    },
    ("OPT03", 1): {
        "mandrino": ["D105"],
        "tastatore": ["D205"],
        "maschera": ["D305"],
        "testa": ["D405"],
        "generatore": ["D505"],
    },
    ("OPT03", 2): {
        "mandrino": ["D106"],
        "tastatore": ["D206"],
        "maschera": ["D306"],
        "testa": ["D406"],
        "generatore": ["D506"],
    },
}


class Command(BaseCommand):
    """Create OPT01–OPT03 demo commesse for optimization testing."""

    help = (
        "Seed 3 demo commesse (2 chained activities each) with an initial multi-week "
        "gap, then OPT01 alone, then OPT02/OPT03 in parallel."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove existing OPT01/OPT02/OPT03 jobs and activities before seeding.",
        )

    def handle(self, *args, **options) -> None:
        with transaction.atomic():
            if options["clear"]:
                self._clear_demo()
            self._seed_reference_data()
            self._seed_inventory()
            jobs_created, acts_created = self._seed_planning()

        self.stdout.write(
            self.style.SUCCESS(
                f"Pianificazione demo creata: {jobs_created} commesse, "
                f"{acts_created} attività ({', '.join(_COMMESSE)})."
            )
        )
        self.stdout.write(
            "Timeline: buco fino al 07/08/2026, poi OPT01 (10-21/08), "
            "poi OPT02+OPT03 in parallelo (24/08-04/09)."
        )

    def _clear_demo(self) -> None:
        Attivita.objects.filter(commessa__in=_COMMESSE).delete()
        Job.objects.filter(commessa__in=_COMMESSE).delete()

    def _seed_reference_data(self) -> None:
        Stabilimento.objects.update_or_create(
            nome="Valbrembo",
            defaults={"colore": "#16a34a"},
        )
        cfg, _ = ConfigurazioneTesta.objects.update_or_create(
            modello_testa="TP60",
            defaults={
                "min_mandrini": 1,
                "min_tastatori": 1,
                "min_maschere": 1,
                "min_generatori": 1,
            },
        )
        GeneratoreCompatibile.objects.get_or_create(
            configurazione=cfg,
            modello_generatore="P6",
        )

    def _seed_inventory(self) -> None:
        plant = "Valbrembo"
        for uid in ("D101", "D102", "D103", "D104", "D105", "D106"):
            MandrinoItem.objects.update_or_create(
                uid=uid,
                defaults={"diameter": "15.75", "plant": plant, "notes": "demo opt"},
            )
        for uid in ("D201", "D202", "D203", "D204", "D205", "D206"):
            TastatoreItem.objects.update_or_create(
                uid=uid,
                defaults={"diameter": "15.75", "plant": plant, "notes": "demo opt"},
            )
        for uid in ("D301", "D302", "D303", "D304", "D305", "D306"):
            MascheraItem.objects.update_or_create(
                uid=uid,
                defaults={"pitch": "25.4", "plant": plant, "center_distance": "127"},
            )
        for uid in ("D401", "D402", "D403", "D404", "D405", "D406"):
            TestaItem.objects.update_or_create(
                uid=uid,
                defaults={"model": "TP60", "plant": plant, "notes": "demo opt"},
            )
        for uid in ("D501", "D502", "D503", "D504", "D505", "D506"):
            GeneratoreItem.objects.update_or_create(
                uid=uid,
                defaults={"model": "P6", "plant": plant, "notes": "demo opt"},
            )

    def _seed_planning(self) -> tuple[int, int]:
        jobs_created = 0
        acts_created = 0
        activity_by_key: dict[tuple[str, int], Attivita] = {}

        for commessa in _COMMESSE:
            meta = _JOB_DEFAULTS[commessa]
            _, created = Job.objects.update_or_create(
                commessa=commessa,
                defaults=meta,
            )
            if created:
                jobs_created += 1

        for commessa, item, name, start, end, pred_item in _ACTIVITY_PLAN:
            pred_id = None
            if pred_item is not None:
                predecessor = activity_by_key.get((commessa, pred_item))
                if predecessor is not None:
                    pred_id = predecessor.activity_id

            att, created = Attivita.objects.update_or_create(
                commessa=commessa,
                item=item,
                activity=name,
                defaults={
                    **_ACTIVITY_DEFAULTS,
                    "data_inizio": start,
                    "data_fine": end,
                    "predecessors": pred_id,
                },
            )
            if created:
                acts_created += 1
            activity_by_key[(commessa, item)] = att

            RisorsaAttivita.objects.filter(attivita=att).delete()
            for tipo, uids in _RESOURCE_PLAN[(commessa, item)].items():
                for uid in uids:
                    RisorsaAttivita.objects.create(
                        attivita=att,
                        tipo=tipo,
                        risorsa_id=uid,
                    )

        return jobs_created, acts_created
