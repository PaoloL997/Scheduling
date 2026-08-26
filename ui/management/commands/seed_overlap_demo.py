"""Seed two activities on different jobs that share a delayed resource.

Scenario (calendar days around 24 ago 2026):
  - Commessa C101, Saldatura: planned 10–19 ago, started 17 ago
    → effective finish 26 ago
  - Commessa C205, Montaggio: planned 20–29 ago, same testa OV-T1
  - Spare testa OV-T2 is auto-assigned to C205; the swap is stored as a note

Run: ``python manage.py seed_overlap_demo``.
Use ``--clear`` to remove the demo commesse (jobs and activities) without
reseeding.

Use ``--reschedule`` for the same delay scenario without a spare testa
(commesse R301/R305, modello TP80, unica testa RS-T1). R301 has a short
chain around the late Saldatura; R305 has Montaggio blocked on RS-T1 plus
Collaudo/Imballo linked by precedence and an independent Documentazione
that must stay put. The optimizer proposes a new date for the blocked
chain, viewable/acceptable from Pianificazione → Note.
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

# Reschedule demo: same delay calendar as C101/C205, but no spare testa, so
# the swap fails and the optimizer proposes a new date for the blocked activity.
_RS_LATE_COMMESSA = "R301"
_RS_BLOCKED_COMMESSA = "R305"
_RS_HEAD_MODEL = "TP80"
_RS_STD_MODEL = "STD"
_RS_TESTA = "RS-T1"

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
        parser.add_argument(
            "--reschedule",
            action="store_true",
            help=(
                "Seed the no-alternative scenario (commesse R301/R305) that "
                "triggers an automatic reschedule proposal instead of a swap."
            ),
        )

    def handle(self, *args, **options) -> None:
        if options["reschedule"] and not options["clear"]:
            self._handle_reschedule()
            return

        with transaction.atomic():
            self._clear_demo()
            if options["clear"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        "Demo sovrapposizione rimossa: commesse "
                        f"{_LATE_COMMESSA}, {_BLOCKED_COMMESSA}, "
                        f"{_RS_LATE_COMMESSA}, {_RS_BLOCKED_COMMESSA}."
                    )
                )
                return
            self._seed_reference()
            self._seed_inventory()
            late, blocked = self._seed_activities()

        from ui.services.overlap_reassign import auto_resolve_resource_overlaps

        auto_resolve_resource_overlaps()
        blocked.refresh_from_db()
        swapped = list(
            RisorsaAttivita.objects.filter(attivita=blocked, tipo="testa").values_list(
                "risorsa_id", flat=True
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo sovrapposizione creata: {late.activity_id} "
                f"(inizio effettivo {late.actual_start}) condivideva la testa "
                f"{_SHARED_TESTA} con {blocked.activity_id}."
            )
        )
        if swapped and swapped != [_SHARED_TESTA]:
            self.stdout.write(
                f"Sostituzione automatica: testa {swapped[0]} su {blocked.activity_id}. "
                "Apri Pianificazione su C205, poi Note."
            )
        else:
            self.stdout.write("Nessuna alternativa: problema aperto su C101 / C205.")

    def _handle_reschedule(self) -> None:
        """Seed the no-swap scenario and print the generated reschedule proposal."""
        from ui.models import ProblemaAttivita

        with transaction.atomic():
            self._clear_reschedule_demo()
            self._seed_reschedule_reference()
            self._seed_reschedule_inventory()
            late, blocked = self._seed_reschedule_activities()

        from ui.services.overlap_reassign import auto_resolve_resource_overlaps

        auto_resolve_resource_overlaps()

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo ripianificazione creata: {late.activity_id} "
                f"(inizio effettivo {late.actual_start}) blocca la testa "
                f"{_RS_TESTA}, unica del modello {_RS_HEAD_MODEL}, "
                f"usata anche da {blocked.activity_id}."
            )
        )

        row = (
            ProblemaAttivita.objects.filter(
                attivita=blocked,
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
                risolto=False,
            )
            .exclude(proposta_json__isnull=True)
            .first()
        )
        proposta = row.proposta_json if row else None
        if proposta and proposta.get("status") == "proposed":
            b = proposta["blocked"]
            deadline = (
                "entro scadenza" if proposta.get("meets_deadline") else "oltre scadenza"
            )
            moved = proposta.get("moved_count") or 1
            extra = f" (sposta {moved} attivita della catena)" if moved > 1 else ""
            self.stdout.write(
                f"Proposta automatica: sposta {blocked.activity_id} a "
                f"{b['start']} -> {b['finish']} ({deadline}){extra}, risorse "
                f"{', '.join(b.get('resource_labels') or b.get('resources') or [])}. "
                "Apri Pianificazione su R305, poi Note per Accetta/Rifiuta."
            )
        else:
            self.stdout.write(
                "Nessuna proposta generata (verifica orizzonte/fattibilità)."
            )

    def _clear_demo(self) -> None:
        commesse = (
            _LATE_COMMESSA,
            _BLOCKED_COMMESSA,
            _LEGACY_COMMESSA,
            _RS_LATE_COMMESSA,
            _RS_BLOCKED_COMMESSA,
        )
        Attivita.objects.filter(commessa__in=commesse).delete()
        Job.objects.filter(commessa__in=commesse).delete()

    def _clear_reschedule_demo(self) -> None:
        commesse = (_RS_LATE_COMMESSA, _RS_BLOCKED_COMMESSA)
        Attivita.objects.filter(commessa__in=commesse).delete()
        Job.objects.filter(commessa__in=commesse).delete()

    def _seed_reschedule_reference(self) -> None:
        Stabilimento.objects.update_or_create(
            nome=_PLANT,
            defaults={"colore": "#16a34a"},
        )
        # Same minima as the main optimizer: one of each implied type.
        # Scarcity is the single TP80 testa RS-T1 (no spare of that model).
        for modello in (_RS_HEAD_MODEL, _RS_STD_MODEL):
            cfg, _ = ConfigurazioneTesta.objects.update_or_create(
                modello_testa=modello,
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

    def _seed_reschedule_inventory(self) -> None:
        TestaItem.objects.update_or_create(
            uid=_RS_TESTA,
            defaults={
                "model": _RS_HEAD_MODEL,
                "plant": _PLANT,
                "notes": "demo reschedule (unica testa TP80)",
            },
        )
        for uid in ("RS-T-TAG", "RS-T-PUL", "RS-T-DOC"):
            TestaItem.objects.update_or_create(
                uid=uid,
                defaults={
                    "model": _RS_STD_MODEL,
                    "plant": _PLANT,
                    "notes": "demo reschedule (STD)",
                },
            )
        for uid in (
            "RS-C1",
            "RS-C2",
            "RS-C3",
            "RS-C4",
            "RS-C5",
            "RS-C6",
            "RS-C7",
        ):
            MandrinoItem.objects.update_or_create(
                uid=uid,
                defaults={
                    "diameter": "15.75",
                    "plant": _PLANT,
                    "notes": "demo reschedule",
                },
            )
        for uid in (
            "RS-TA1",
            "RS-TA2",
            "RS-TA3",
            "RS-TA4",
            "RS-TA5",
            "RS-TA6",
            "RS-TA7",
        ):
            TastatoreItem.objects.update_or_create(
                uid=uid,
                defaults={
                    "diameter": "15.75",
                    "plant": _PLANT,
                    "notes": "demo reschedule",
                },
            )
        for uid in ("RS-M1", "RS-M2", "RS-M3", "RS-M4", "RS-M5", "RS-M6", "RS-M7"):
            MascheraItem.objects.update_or_create(
                uid=uid,
                defaults={"pitch": "25.4", "plant": _PLANT, "center_distance": "127"},
            )
        for uid in ("RS-G1", "RS-G2", "RS-G3", "RS-G4", "RS-G5", "RS-G6", "RS-G7"):
            GeneratoreItem.objects.update_or_create(
                uid=uid,
                defaults={"model": "P6", "plant": _PLANT, "notes": "demo reschedule"},
            )

    def _assign_resources(self, att: Attivita, plan: dict[str, list[str]]) -> None:
        RisorsaAttivita.objects.filter(attivita=att).delete()
        for tipo, uids in plan.items():
            for uid in uids:
                RisorsaAttivita.objects.create(attivita=att, tipo=tipo, risorsa_id=uid)

    def _seed_reschedule_activity(
        self,
        commessa: str,
        item: int,
        activity: str,
        *,
        start: date,
        finish: date,
        duration_min: int,
        modello_testa: str,
        actual_start: date | None = None,
        actual_finish: date | None = None,
    ) -> Attivita:
        att, _ = Attivita.objects.update_or_create(
            commessa=commessa,
            item=item,
            activity=activity,
            defaults={
                "stabilimento": _PLANT,
                "responsabile": "Demo Reschedule",
                "modello_testa": modello_testa,
                "bore_min": "14.99",
                "bore_max": "16.51",
                "bore_mid": "15.75",
                "tube_pitch": "25.4",
                "duration_min": duration_min,
                "imported_from_mpp": False,
                "synced_to_mpp": False,
                "data_inizio": start,
                "data_fine": finish,
                "actual_start": actual_start,
                "actual_finish": actual_finish,
                "predecessors": "",
            },
        )
        return att

    def _seed_reschedule_activities(self) -> tuple[Attivita, Attivita]:
        Job.objects.update_or_create(
            commessa=_RS_LATE_COMMESSA,
            defaults={
                "total_cost": 120_000.0,
                "penalty": 2.0,
                "delivery_date": "2026-09-15",
            },
        )
        Job.objects.update_or_create(
            commessa=_RS_BLOCKED_COMMESSA,
            defaults={
                "total_cost": 90_000.0,
                "penalty": 1.5,
                "delivery_date": "2026-09-30",
            },
        )

        # R301: completed Taglio, then Saldatura started 7 days late (unique
        # testa RS-T1 occupied until 26 ago), then Pulizia still to start.
        taglio = self._seed_reschedule_activity(
            _RS_LATE_COMMESSA,
            1,
            "Taglio",
            start=date(2026, 8, 3),
            finish=date(2026, 8, 9),
            duration_min=2400,
            modello_testa=_RS_STD_MODEL,
            actual_start=date(2026, 8, 3),
            actual_finish=date(2026, 8, 9),
        )
        late = self._seed_reschedule_activity(
            _RS_LATE_COMMESSA,
            1,
            "Saldatura",
            start=date(2026, 8, 10),
            finish=date(2026, 8, 19),
            duration_min=2400,
            modello_testa=_RS_HEAD_MODEL,
            actual_start=date(2026, 8, 17),
        )
        pulizia = self._seed_reschedule_activity(
            _RS_LATE_COMMESSA,
            1,
            "Pulizia",
            start=date(2026, 8, 20),
            finish=date(2026, 8, 25),
            duration_min=1440,
            modello_testa=_RS_STD_MODEL,
        )

        # R305: Montaggio was sequential after Saldatura's planned finish, so
        # the overlap is delay-caused. Collaudo and Imballo follow it on the
        # same TP80 testa; Documentazione is independent (STD kit) and stays.
        blocked = self._seed_reschedule_activity(
            _RS_BLOCKED_COMMESSA,
            1,
            "Montaggio",
            start=date(2026, 8, 20),
            finish=date(2026, 8, 29),
            duration_min=2400,
            modello_testa=_RS_HEAD_MODEL,
        )
        collaudo = self._seed_reschedule_activity(
            _RS_BLOCKED_COMMESSA,
            1,
            "Collaudo",
            start=date(2026, 9, 1),
            finish=date(2026, 9, 5),
            duration_min=1440,
            modello_testa=_RS_HEAD_MODEL,
        )
        imballo = self._seed_reschedule_activity(
            _RS_BLOCKED_COMMESSA,
            1,
            "Imballo",
            start=date(2026, 9, 8),
            finish=date(2026, 9, 12),
            duration_min=960,
            modello_testa=_RS_HEAD_MODEL,
        )
        docs = self._seed_reschedule_activity(
            _RS_BLOCKED_COMMESSA,
            2,
            "Documentazione",
            start=date(2026, 8, 18),
            finish=date(2026, 8, 22),
            duration_min=960,
            modello_testa=_RS_STD_MODEL,
        )

        late.predecessors = taglio.activity_id
        late.save(update_fields=["predecessors"])
        pulizia.predecessors = late.activity_id
        pulizia.save(update_fields=["predecessors"])
        collaudo.predecessors = blocked.activity_id
        collaudo.save(update_fields=["predecessors"])
        imballo.predecessors = collaudo.activity_id
        imballo.save(update_fields=["predecessors"])

        # Unique TP80 testa RS-T1 is shared only along the delayed chain
        # (sequential in the original plan). STD activities have their own kit
        # so problem detection does not raise "da recuperare".
        self._assign_resources(
            taglio,
            {
                "testa": ["RS-T-TAG"],
                "mandrino": ["RS-C1"],
                "tastatore": ["RS-TA1"],
                "maschera": ["RS-M1"],
                "generatore": ["RS-G1"],
            },
        )
        self._assign_resources(
            late,
            {
                "testa": [_RS_TESTA],
                "mandrino": ["RS-C2"],
                "tastatore": ["RS-TA2"],
                "maschera": ["RS-M2"],
                "generatore": ["RS-G2"],
            },
        )
        self._assign_resources(
            pulizia,
            {
                "testa": ["RS-T-PUL"],
                "mandrino": ["RS-C3"],
                "tastatore": ["RS-TA3"],
                "maschera": ["RS-M3"],
                "generatore": ["RS-G3"],
            },
        )
        self._assign_resources(
            blocked,
            {
                "testa": [_RS_TESTA],
                "mandrino": ["RS-C4"],
                "tastatore": ["RS-TA4"],
                "maschera": ["RS-M4"],
                "generatore": ["RS-G4"],
            },
        )
        self._assign_resources(
            collaudo,
            {
                "testa": [_RS_TESTA],
                "mandrino": ["RS-C5"],
                "tastatore": ["RS-TA5"],
                "maschera": ["RS-M5"],
                "generatore": ["RS-G5"],
            },
        )
        self._assign_resources(
            imballo,
            {
                "testa": [_RS_TESTA],
                "mandrino": ["RS-C6"],
                "tastatore": ["RS-TA6"],
                "maschera": ["RS-M6"],
                "generatore": ["RS-G6"],
            },
        )
        self._assign_resources(
            docs,
            {
                "testa": ["RS-T-DOC"],
                "mandrino": ["RS-C7"],
                "tastatore": ["RS-TA7"],
                "maschera": ["RS-M7"],
                "generatore": ["RS-G7"],
            },
        )

        return late, blocked

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
