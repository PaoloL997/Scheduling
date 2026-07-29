"""Tests for operational problem detection on activity save."""

from datetime import date

from django.test import TestCase

from ui.models import (
    Attivita,
    ConfigurazioneTesta,
    ProblemaAttivita,
    RisoluzioneProblema,
    RisorsaAttivita,
)
from ui.services.activity_inventory import get_testa_min_requirements
from ui.services.problem_detection import (
    detect_and_save_problems,
    detect_overlap_problems,
    effective_finish,
    parse_blocked_resources,
    reopen_stale_overlap_resolutions,
    resolve_stale_overlaps,
    restore_resolved_notes,
)


class GetTestaMinRequirementsTest(TestCase):
    """get_testa_min_requirements reads ConfigurazioneTesta minimum counts."""

    def test_defaults_when_no_configuration(self) -> None:
        self.assertEqual(
            get_testa_min_requirements("UNKNOWN"),
            {
                "teste": 1,
                "mandrini": 1,
                "tastatori": 1,
                "maschere": 1,
                "generatori": 1,
            },
        )

    def test_reads_configured_minimums(self) -> None:
        ConfigurazioneTesta.objects.create(
            modello_testa="IRIS",
            min_mandrini=2,
            min_tastatori=1,
            min_maschere=3,
            min_generatori=2,
        )
        self.assertEqual(
            get_testa_min_requirements("IRIS"),
            {
                "teste": 1,
                "mandrini": 2,
                "tastatori": 1,
                "maschere": 3,
                "generatori": 2,
            },
        )


class DetectShortfallProblemsTest(TestCase):
    """detect_and_save_problems flags under-selected resource categories."""

    def setUp(self) -> None:
        ConfigurazioneTesta.objects.create(
            modello_testa="IRIS",
            min_mandrini=2,
            min_maschere=2,
        )
        self.att = Attivita.objects.create(
            commessa="C001",
            item=1,
            activity="Saldatura",
            modello_testa="IRIS",
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
        )

    def test_empty_category_creates_acquisto_problem_with_minimum(self) -> None:
        count = detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": ["M1", "M2"],
                "tastatori": ["TA1"],
                "maschere": [],
                "generatori": ["G1"],
            },
        )
        self.assertEqual(count, 1)
        problema = ProblemaAttivita.objects.get(
            attivita=self.att, risorsa_tipo="maschera"
        )
        self.assertEqual(problema.tipo, ProblemaAttivita.TIPO_ACQUISTO)
        self.assertIn("0/2", problema.descrizione)
        self.assertIn("IRIS", problema.descrizione)

    def test_partial_selection_creates_shortfall_problem(self) -> None:
        count = detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": ["M1"],
                "tastatori": ["TA1"],
                "maschere": ["MA1", "MA2"],
                "generatori": ["G1"],
            },
        )
        self.assertEqual(count, 1)
        problema = ProblemaAttivita.objects.get(
            attivita=self.att, risorsa_tipo="mandrino"
        )
        self.assertIn("1/2", problema.descrizione)
        self.assertIn("manca", problema.descrizione.lower())

    def test_full_selection_creates_no_shortfall_problems(self) -> None:
        count = detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": ["M1", "M2"],
                "tastatori": ["TA1"],
                "maschere": ["MA1", "MA2"],
                "generatori": ["G1"],
            },
        )
        self.assertEqual(count, 0)
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.att, tipo=ProblemaAttivita.TIPO_ACQUISTO
            ).exists()
        )

    def test_full_selection_auto_resolves_stale_shortfall(self) -> None:
        detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": ["M1"],
                "tastatori": ["TA1"],
                "maschere": ["MA1", "MA2"],
                "generatori": ["G1"],
            },
        )
        self.assertEqual(
            ProblemaAttivita.objects.filter(
                attivita=self.att, tipo=ProblemaAttivita.TIPO_ACQUISTO, risolto=False
            ).count(),
            1,
        )

        detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": ["M1", "M2"],
                "tastatori": ["TA1"],
                "maschere": ["MA1", "MA2"],
                "generatori": ["G1"],
            },
        )
        open_acq = ProblemaAttivita.objects.filter(
            attivita=self.att, tipo=ProblemaAttivita.TIPO_ACQUISTO, risolto=False
        )
        self.assertFalse(open_acq.exists())
        closed = ProblemaAttivita.objects.get(
            attivita=self.att, tipo=ProblemaAttivita.TIPO_ACQUISTO, risolto=True
        )
        self.assertIn("non piu verificata", closed.note_risoluzione.lower())

    def test_shortfall_description_updates_when_selection_changes(self) -> None:
        detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": [],
                "tastatori": ["TA1"],
                "maschere": ["MA1", "MA2"],
                "generatori": ["G1"],
            },
        )
        problema = ProblemaAttivita.objects.get(
            attivita=self.att, risorsa_tipo="mandrino", risolto=False
        )
        self.assertIn("0/2", problema.descrizione)

        detect_and_save_problems(
            self.att,
            {
                "teste": ["T1"],
                "mandrini": ["M1"],
                "tastatori": ["TA1"],
                "maschere": ["MA1", "MA2"],
                "generatori": ["G1"],
            },
        )
        problema.refresh_from_db()
        self.assertFalse(problema.risolto)
        self.assertIn("1/2", problema.descrizione)

    def test_shortfall_reappears_after_auto_resolve(self) -> None:
        full = {
            "teste": ["T1"],
            "mandrini": ["M1", "M2"],
            "tastatori": ["TA1"],
            "maschere": ["MA1", "MA2"],
            "generatori": ["G1"],
        }
        short = {
            "teste": ["T1"],
            "mandrini": ["M1"],
            "tastatori": ["TA1"],
            "maschere": ["MA1", "MA2"],
            "generatori": ["G1"],
        }
        detect_and_save_problems(self.att, short)
        detect_and_save_problems(self.att, full)
        created = detect_and_save_problems(self.att, short)
        self.assertEqual(created, 1)
        self.assertEqual(
            ProblemaAttivita.objects.filter(
                attivita=self.att,
                tipo=ProblemaAttivita.TIPO_ACQUISTO,
                risolto=False,
            ).count(),
            1,
        )


class DetectSpostamentoProblemsTest(TestCase):
    """detect_and_save_problems flags and clears wrong-plant assignments."""

    def setUp(self) -> None:
        from ui.models import TestaItem

        TestaItem.objects.create(uid="T1", model="IRIS", plant="Osio")
        self.att = Attivita.objects.create(
            commessa="C001",
            item=1,
            activity="Saldatura",
            modello_testa="IRIS",
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
        )
        self.risorse = {
            "teste": ["T1"],
            "mandrini": ["M1"],
            "tastatori": ["TA1"],
            "maschere": ["MA1"],
            "generatori": ["G1"],
        }

    def test_wrong_plant_creates_spostamento(self) -> None:
        count = detect_and_save_problems(self.att, self.risorse)
        self.assertEqual(count, 1)
        problema = ProblemaAttivita.objects.get(
            attivita=self.att, tipo=ProblemaAttivita.TIPO_SPOSTAMENTO
        )
        self.assertEqual(problema.risorsa_uid, "T1")
        self.assertIn("Osio", problema.descrizione)

    def test_aligned_plant_auto_resolves_spostamento(self) -> None:
        detect_and_save_problems(self.att, self.risorse)
        self.att.stabilimento = "Osio"
        self.att.save(update_fields=["stabilimento"])
        detect_and_save_problems(self.att, self.risorse)
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.att,
                tipo=ProblemaAttivita.TIPO_SPOSTAMENTO,
                risolto=False,
            ).exists()
        )


class DetectForAllActivitiesRefreshTest(TestCase):
    """detect_for_all_activities reconciles stale open notes from DB resources."""

    def setUp(self) -> None:
        ConfigurazioneTesta.objects.create(
            modello_testa="IRIS",
            min_mandrini=2,
            min_maschere=2,
        )
        self.att = Attivita.objects.create(
            commessa="C001",
            item=1,
            activity="Saldatura",
            modello_testa="IRIS",
            stabilimento="Valbrembo",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
        )
        for tipo, uid in (
            ("testa", "T1"),
            ("mandrino", "M1"),
            ("mandrino", "M2"),
            ("tastatore", "TA1"),
            ("maschera", "MA1"),
            ("maschera", "MA2"),
            ("generatore", "G1"),
        ):
            RisorsaAttivita.objects.create(
                attivita=self.att, tipo=tipo, risorsa_id=uid
            )
        ProblemaAttivita.objects.create(
            attivita=self.att,
            tipo=ProblemaAttivita.TIPO_ACQUISTO,
            risorsa_tipo="mandrino",
            risorsa_uid="",
            descrizione="stale shortfall",
        )

    def test_detect_for_all_closes_stale_acquisto(self) -> None:
        from ui.services.problem_detection import detect_for_all_activities

        detect_for_all_activities()
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.att,
                tipo=ProblemaAttivita.TIPO_ACQUISTO,
                risolto=False,
            ).exists()
        )


class DetectOverlapProblemsTest(TestCase):
    """detect_overlap_problems flags successors sharing occupied resources."""

    def setUp(self) -> None:
        # X: planned 01/06 - 10/06 (9-day span), holds testa T1.
        self.x = Attivita.objects.create(
            commessa="C001",
            item=1,
            activity="Montaggio",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
        )
        RisorsaAttivita.objects.create(attivita=self.x, tipo="testa", risorsa_id="T1")
        # Y: planned to start 11/06, right after X's planned finish, same testa.
        self.y = Attivita.objects.create(
            commessa="C002",
            item=1,
            activity="Saldatura",
            data_inizio=date(2026, 6, 11),
            data_fine=date(2026, 6, 20),
        )
        RisorsaAttivita.objects.create(attivita=self.y, tipo="testa", risorsa_id="T1")

    def _start_x(self, day: date) -> None:
        self.x.actual_start = day
        self.x.save(update_fields=["actual_start"])

    def test_effective_finish_shifts_by_planned_span(self) -> None:
        self._start_x(date(2026, 6, 5))
        self.assertEqual(effective_finish(self.x), date(2026, 6, 14))

    def test_late_start_creates_overlap_note_on_successor(self) -> None:
        self._start_x(date(2026, 6, 5))  # effective finish 14/06 > Y start 11/06
        created = detect_overlap_problems(self.x)
        self.assertEqual(len(created), 1)
        self.assertEqual(
            created[0]["risorse_bloccate"], [{"tipo": "testa", "uid": "T1"}]
        )
        problema = ProblemaAttivita.objects.get(
            attivita=self.y, tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE
        )
        self.assertEqual(problema.risorsa_uid, "testa:T1")
        self.assertEqual(problema.risorsa_tipo, "")
        self.assertIn("Testa T1", problema.descrizione)
        self.assertEqual(problema.bloccata_fino, date(2026, 6, 14))
        self.assertEqual(problema.riferimento_attivita, self.x.activity_id)
        self.assertFalse(problema.risolto)

    def test_multiple_blocked_resources_create_single_note(self) -> None:
        RisorsaAttivita.objects.create(
            attivita=self.x, tipo="mandrino", risorsa_id="M1"
        )
        RisorsaAttivita.objects.create(
            attivita=self.y, tipo="mandrino", risorsa_id="M1"
        )
        self._start_x(date(2026, 6, 5))
        detect_overlap_problems(self.x)
        problemi = ProblemaAttivita.objects.filter(
            attivita=self.y, tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE
        )
        self.assertEqual(problemi.count(), 1)
        problema = problemi.get()
        blocked = parse_blocked_resources(problema.risorsa_uid, problema.risorsa_tipo)
        self.assertEqual(set(blocked), {("testa", "T1"), ("mandrino", "M1")})
        self.assertIn("Testa T1", problema.descrizione)
        self.assertIn("Mandrino M1", problema.descrizione)

    def test_on_time_start_creates_no_overlap(self) -> None:
        self._start_x(date(2026, 6, 1))  # effective finish 10/06, Y starts 11/06
        created = detect_overlap_problems(self.x)
        self.assertEqual(created, [])
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE
            ).exists()
        )

    def test_no_overlap_when_resources_differ(self) -> None:
        RisorsaAttivita.objects.filter(attivita=self.y).delete()
        RisorsaAttivita.objects.create(attivita=self.y, tipo="testa", risorsa_id="T2")
        self._start_x(date(2026, 6, 5))
        created = detect_overlap_problems(self.x)
        self.assertEqual(created, [])

    def test_detection_is_idempotent(self) -> None:
        self._start_x(date(2026, 6, 5))
        detect_overlap_problems(self.x)
        detect_overlap_problems(self.x)
        self.assertEqual(
            ProblemaAttivita.objects.filter(
                attivita=self.y, tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE
            ).count(),
            1,
        )

    def test_resolve_stale_overlaps_closes_note_after_reassignment(self) -> None:
        self._start_x(date(2026, 6, 5))
        detect_overlap_problems(self.x)
        # Y reassigns away from the conflicting testa T1.
        RisorsaAttivita.objects.filter(attivita=self.y).delete()
        RisorsaAttivita.objects.create(attivita=self.y, tipo="testa", risorsa_id="T9")
        resolved = resolve_stale_overlaps(self.y)
        self.assertEqual(resolved, 1)
        problema = ProblemaAttivita.objects.get(
            attivita=self.y, tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE
        )
        self.assertTrue(problema.risolto)

    def test_redetects_overlap_after_resolved_note_when_conflict_persists(self) -> None:
        """A resolved note must not block re-detection when overlap still exists."""
        self._start_x(date(2026, 6, 5))
        detect_overlap_problems(self.x)
        problema = ProblemaAttivita.objects.get(
            attivita=self.y, tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE
        )
        problema.risolto = True
        problema.save(update_fields=["risolto"])

        created = detect_overlap_problems(self.x)
        self.assertEqual(len(created), 1)
        self.assertEqual(
            ProblemaAttivita.objects.filter(
                attivita=self.y,
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
                risolto=False,
            ).count(),
            1,
        )


class RestoreResolvedOverlapNotesTest(TestCase):
    """restore_resolved_notes must not hide active overlaps after re-import."""

    def setUp(self) -> None:
        self.x = Attivita.objects.create(
            commessa="C001",
            item=1,
            activity="Montaggio",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
            actual_start=date(2026, 6, 5),
        )
        self.y = Attivita.objects.create(
            commessa="C002",
            item=1,
            activity="Saldatura",
            data_inizio=date(2026, 6, 11),
            data_fine=date(2026, 6, 20),
        )
        RisorsaAttivita.objects.create(attivita=self.x, tipo="testa", risorsa_id="T1")
        RisorsaAttivita.objects.create(attivita=self.y, tipo="testa", risorsa_id="T1")

    def test_reassignment_resolution_not_restored_when_blocked_resource_remains(self):
        detect_overlap_problems(self.x)
        problema = ProblemaAttivita.objects.get(attivita=self.y)
        RisoluzioneProblema.objects.create(
            commessa=self.y.commessa,
            item=self.y.item,
            activity=self.y.activity,
            tipo=problema.tipo,
            risorsa_tipo=problema.risorsa_tipo,
            risorsa_uid=problema.risorsa_uid,
            note_risoluzione="Risorsa riassegnata: la risorsa in conflitto non e piu assegnata.",
        )
        problema.delete()
        detect_overlap_problems(self.x)
        restore_resolved_notes()
        self.assertTrue(
            ProblemaAttivita.objects.filter(
                attivita=self.y,
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
                risolto=False,
            ).exists()
        )

    def test_accepted_overlap_stays_resolved_after_restore(self):
        detect_overlap_problems(self.x)
        problema = ProblemaAttivita.objects.get(attivita=self.y)
        RisoluzioneProblema.objects.create(
            commessa=self.y.commessa,
            item=self.y.item,
            activity=self.y.activity,
            tipo=problema.tipo,
            risorsa_tipo=problema.risorsa_tipo,
            risorsa_uid=problema.risorsa_uid,
            note_risoluzione="Sovrapposizione accettata (lasciato invariato).",
        )
        problema.delete()
        detect_overlap_problems(self.x)
        restore_resolved_notes()
        self.assertTrue(
            ProblemaAttivita.objects.filter(
                attivita=self.y,
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
                risolto=True,
            ).exists()
        )
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.y,
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
                risolto=False,
            ).exists()
        )


class ReopenStaleOverlapResolutionsTest(TestCase):
    """Resolved overlap notes must reopen when the conflict is still active."""

    def setUp(self) -> None:
        self.x = Attivita.objects.create(
            commessa="C001",
            item=1,
            activity="Montaggio",
            activity_id="C001-01-Montaggio",
            data_inizio=date(2026, 6, 1),
            data_fine=date(2026, 6, 10),
            actual_start=date(2026, 6, 5),
        )
        self.y = Attivita.objects.create(
            commessa="C002",
            item=1,
            activity="Saldatura",
            data_inizio=date(2026, 6, 11),
            data_fine=date(2026, 6, 20),
        )
        RisorsaAttivita.objects.create(attivita=self.x, tipo="testa", risorsa_id="T1")
        RisorsaAttivita.objects.create(attivita=self.y, tipo="testa", risorsa_id="T1")

    def test_auto_resolved_note_reopens_when_conflict_persists(self) -> None:
        detect_overlap_problems(self.x)
        problema = ProblemaAttivita.objects.get(attivita=self.y)
        problema.risolto = True
        problema.note_risoluzione = (
            "Risorsa riassegnata: la risorsa in conflitto non e piu assegnata."
        )
        problema.save(update_fields=["risolto", "note_risoluzione"])

        reopened = reopen_stale_overlap_resolutions()

        self.assertEqual(reopened, 1)
        problema.refresh_from_db()
        self.assertFalse(problema.risolto)
