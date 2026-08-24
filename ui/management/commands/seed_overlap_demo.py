"""Seed two activities on different jobs that share a delayed resource.

Scenario (calendar days around 24 ago 2026):
  - Commessa C101, Saldatura: planned 10–19 ago, started 17 ago
    → effective finish 26 ago
  - Commessa C205, Montaggio: planned 20–29 ago, same testa OV-T1
  - Overlap window: 20–26 ago (no predecessor link)

Run: ``python manage.py seed_overlap_demo``.
Use ``--clear`` to remove the demo commesse (jobs and activities) without
reseeding.
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

_LATE_COMMESSA = "C101"
_BLOCKED_COMMESSA = "C205"
_LEGACY_COMMESSA = "OVLAP"
_PLANT = "Valbrembo"
_HEAD_MODEL = "TP60"
_SHARED_TESTA = "OV-T1"

_ACTIVITY_DEFAULTS = {
    "stabilimento": _PLANT,
    "responsabile": "Demo Overlap",
    "modello_testa": _HEAD_MODEL,
    "bore_min": "14.99",
    "bore_max": "16.51",
    "bore_mid": "15.75",
    "tube_pitch": "25.4",
    "duration_min": 2400,
    "imported_from_mpp": False,
    "synced_to_mpp": False,
}


class Command(BaseCommand):
    """Create a cross-job delay overlap on C101 (late) and C205 (blocked)."""

    help = (
        "Seed two activities on different commesse that share testa OV-T1, "
        "with the first started late so the second is blocked."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove the demo commesse and their activities, then exit.",
        )

    def handle(self, *args, **options) -> None:
        with transaction.atomic():
            self._clear_demo()
            if options["clear"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        "Demo sovrapposizione rimossa: commesse "
                        f"{_LATE_COMMESSA}, {_BLOCKED_COMMESSA}."
                    )
                )
                return
            self._seed_reference()
            self._seed_inventory()
            late, blocked = self._seed_activities()

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo sovrapposizione creata: {late.activity_id} "
                f"(inizio effettivo {late.actual_start}) e {blocked.activity_id} "
                f"condividono la testa {_SHARED_TESTA}."
            )
        )
        self.stdout.write(
            "Commesse diverse, nessuna dipendenza. "
            "Finestra di conflitto: 20–26 ago 2026. "
            "Apri Pianificazione o il Gantt su C101 / C205."
        )

    def _clear_demo(self) -> None:
        commesse = (_LATE_COMMESSA, _BLOCKED_COMMESSA, _LEGACY_COMMESSA)
        Attivita.objects.filter(commessa__in=commesse).delete()
        Job.objects.filter(commessa__in=commesse).delete()

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
            modello_generatore="P6",
        )

    def _seed_inventory(self) -> None:
        TestaItem.objects.update_or_create(
            uid=_SHARED_TESTA,
            defaults={"model": _HEAD_MODEL, "plant": _PLANT, "notes": "demo overlap"},
        )
        TestaItem.objects.update_or_create(
            uid="OV-T2",
            defaults={
                "model": _HEAD_MODEL,
                "plant": _PLANT,
                "notes": "demo overlap alt",
            },
        )
        for uid in ("OV-C1", "OV-C2"):
            MandrinoItem.objects.update_or_create(
                uid=uid,
                defaults={
                    "diameter": "15.75",
                    "plant": _PLANT,
                    "notes": "demo overlap",
                },
            )
        for uid in ("OV-TA1", "OV-TA2"):
            TastatoreItem.objects.update_or_create(
                uid=uid,
                defaults={
                    "diameter": "15.75",
                    "plant": _PLANT,
                    "notes": "demo overlap",
                },
            )
        for uid in ("OV-M1", "OV-M2"):
            MascheraItem.objects.update_or_create(
                uid=uid,
                defaults={"pitch": "25.4", "plant": _PLANT, "center_distance": "127"},
            )
        for uid in ("OV-G1", "OV-G2"):
            GeneratoreItem.objects.update_or_create(
                uid=uid,
                defaults={"model": "P6", "plant": _PLANT, "notes": "demo overlap"},
            )

    def _seed_activities(self) -> tuple[Attivita, Attivita]:
        Job.objects.update_or_create(
            commessa=_LATE_COMMESSA,
            defaults={
                "total_cost": 150_000.0,
                "penalty": 2.0,
                "delivery_date": "2026-09-15",
            },
        )
        Job.objects.update_or_create(
            commessa=_BLOCKED_COMMESSA,
            defaults={
                "total_cost": 98_000.0,
                "penalty": 1.5,
                "delivery_date": "2026-09-30",
            },
        )

        late, _ = Attivita.objects.update_or_create(
            commessa=_LATE_COMMESSA,
            item=1,
            activity="Saldatura",
            defaults={
                **_ACTIVITY_DEFAULTS,
                "data_inizio": date(2026, 8, 10),
                "data_fine": date(2026, 8, 19),
                "actual_start": date(2026, 8, 17),
                "predecessors": "",
            },
        )
        blocked, _ = Attivita.objects.update_or_create(
            commessa=_BLOCKED_COMMESSA,
            item=1,
            activity="Montaggio",
            defaults={
                **_ACTIVITY_DEFAULTS,
                "data_inizio": date(2026, 8, 20),
                "data_fine": date(2026, 8, 29),
                "actual_start": None,
                "actual_finish": None,
                "predecessors": "",
            },
        )

        late_res = {
            "testa": [_SHARED_TESTA],
            "mandrino": ["OV-C1"],
            "tastatore": ["OV-TA1"],
            "maschera": ["OV-M1"],
            "generatore": ["OV-G1"],
        }
        blocked_res = {
            "testa": [_SHARED_TESTA],
            "mandrino": ["OV-C2"],
            "tastatore": ["OV-TA2"],
            "maschera": ["OV-M2"],
            "generatore": ["OV-G2"],
        }
        for att, plan in ((late, late_res), (blocked, blocked_res)):
            RisorsaAttivita.objects.filter(attivita=att).delete()
            for tipo, uids in plan.items():
                for uid in uids:
                    RisorsaAttivita.objects.create(
                        attivita=att, tipo=tipo, risorsa_id=uid
                    )

        return late, blocked
