"""Tests for overlap auto-reassignment and persisted notes/problems."""

from datetime import date, timedelta

from django.test import Client, TestCase
from django.utils.timezone import localdate

from ui.models import (
    Attivita,
    ConfigurazioneTesta,
    GeneratoreItem,
    Job,
    MandrinoItem,
    ProblemaAttivita,
    RisorsaAttivita,
    TestaItem,
)
from ui.services.optimization_problem import reservations_for_excluded
from ui.services.overlap_reassign import (
    apply_reschedule,
    auto_resolve_resource_overlaps,
    compute_reschedule_proposal,
    build_suggestion,
    propose_reschedule,
    reject_reschedule,
)
from ui.services.problem_detection import (
    detect_for_all_activities,
    find_resource_overlaps,
)
from ui.tests.helpers import write_user


class OverlapReassignTest(TestCase):
    """Suggest and auto-apply a replacement for the blocked activity."""

    def setUp(self) -> None:
        self.late = Attivita.objects.create(
            commessa="C101",
            item=1,
            activity="Saldatura",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 10),
            data_fine=date(2026, 8, 19),
            actual_start=date(2026, 8, 17),
        )
        self.blocked = Attivita.objects.create(
            commessa="C205",
            item=2,
            activity="Montaggio",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 20),
            data_fine=date(2026, 8, 29),
        )
        for att in (self.late, self.blocked):
            RisorsaAttivita.objects.create(
                attivita=att, tipo="testa", risorsa_id="OV-T1"
            )
        TestaItem.objects.create(uid="OV-T1", model="TP60", plant="Valbrembo")

    def _overlap_item(self) -> dict:
        result = find_resource_overlaps()
        return result[self.blocked.pk][0]

    def test_suggests_free_same_plant_testa(self) -> None:
        TestaItem.objects.create(uid="OV-T2", model="TP60", plant="Valbrembo")
        activities = list(Attivita.objects.prefetch_related("risorse"))
        sug = build_suggestion(self.blocked, self._overlap_item(), activities)
        self.assertEqual(len(sug["replacements"]), 1)
        self.assertEqual(sug["replacements"][0]["from_uid"], "OV-T1")
        self.assertEqual(sug["replacements"][0]["to_uid"], "OV-T2")
        self.assertFalse(sug["unresolved"])

    def test_no_alternative_when_inventory_empty(self) -> None:
        activities = list(Attivita.objects.prefetch_related("risorse"))
        sug = build_suggestion(self.blocked, self._overlap_item(), activities)
        self.assertEqual(sug["replacements"], [])
        self.assertEqual(sug["unresolved"][0]["from_uid"], "OV-T1")

    def test_does_not_suggest_incompatible_model(self) -> None:
        TestaItem.objects.create(uid="OV-T9", model="OTHER", plant="Valbrembo")
        activities = list(Attivita.objects.prefetch_related("risorse"))
        sug = build_suggestion(self.blocked, self._overlap_item(), activities)
        self.assertEqual(sug["replacements"], [])

    def test_does_not_suggest_delay_occupied_resource(self) -> None:
        TestaItem.objects.create(uid="OV-T2", model="TP60", plant="Valbrembo")
        other = Attivita.objects.create(
            commessa="OTHER",
            item=1,
            activity="Altro",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 1),
            data_fine=date(2026, 8, 10),
            actual_start=date(2026, 8, 12),
        )
        RisorsaAttivita.objects.create(attivita=other, tipo="testa", risorsa_id="OV-T2")
        activities = list(Attivita.objects.prefetch_related("risorse"))
        sug = build_suggestion(self.blocked, self._overlap_item(), activities)
        self.assertEqual(sug["replacements"], [])

    def test_auto_resolve_swaps_and_writes_resolved_note(self) -> None:
        TestaItem.objects.create(uid="OV-T2", model="TP60", plant="Valbrembo")
        result = auto_resolve_resource_overlaps()
        self.assertIn(self.blocked.pk, result["swapped_blocked_ids"])
        self.assertEqual(
            list(
                RisorsaAttivita.objects.filter(attivita=self.blocked).values_list(
                    "risorsa_id", flat=True
                )
            ),
            ["OV-T2"],
        )
        self.assertEqual(
            list(
                RisorsaAttivita.objects.filter(attivita=self.late).values_list(
                    "risorsa_id", flat=True
                )
            ),
            ["OV-T1"],
        )
        self.assertEqual(find_resource_overlaps(), {})
        note = ProblemaAttivita.objects.get(
            attivita=self.blocked, tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE
        )
        self.assertTrue(note.risolto)
        self.assertIn("OV-T1", note.descrizione)
        self.assertIn("OV-T2", note.descrizione)
        self.assertIn("Sostituzione automatica", note.note_risoluzione)
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE, risolto=False
            ).exists()
        )

    def test_auto_resolve_flags_open_problem_without_alternative(self) -> None:
        result = auto_resolve_resource_overlaps()
        self.assertEqual(result["swapped_blocked_ids"], [])
        self.assertEqual(
            RisorsaAttivita.objects.get(attivita=self.blocked).risorsa_id, "OV-T1"
        )
        open_problems = ProblemaAttivita.objects.filter(
            tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE, risolto=False
        )
        self.assertGreaterEqual(open_problems.count(), 1)
        self.assertTrue(
            any("Nessuna alternativa" in p.descrizione for p in open_problems)
        )
        # The problem is attached to the not-started activity only.
        self.assertTrue(
            ProblemaAttivita.objects.filter(
                attivita=self.blocked,
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
                risolto=False,
            ).exists()
        )
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.late,
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
                risolto=False,
            ).exists()
        )

    def test_detect_keeps_resolved_swap_notes(self) -> None:
        TestaItem.objects.create(uid="OV-T2", model="TP60", plant="Valbrembo")
        auto_resolve_resource_overlaps()
        detect_for_all_activities()
        note = ProblemaAttivita.objects.get(
            attivita=self.blocked,
            tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
            risolto=True,
        )
        self.assertIn("OV-T2", note.descrizione)


class ReservationGeneralizationTest(TestCase):
    """Planned (not-started) activities now reserve their resources."""

    def setUp(self) -> None:
        TestaItem.objects.create(uid="OV-R1", model="TP60", plant="Valbrembo")
        self.planned = Attivita.objects.create(
            commessa="CX",
            item=1,
            activity="Foratura",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2027, 5, 1),
            data_fine=date(2027, 5, 10),
        )
        RisorsaAttivita.objects.create(
            attivita=self.planned, tipo="testa", risorsa_id="OV-R1"
        )

    def test_planned_activity_is_reserved_when_excluded(self) -> None:
        reservations = reservations_for_excluded(date(2027, 1, 1), set())
        self.assertTrue(
            any(r.resource_id == "testa:OV-R1" for r in reservations),
            "A merely-planned activity must reserve its resource.",
        )

    def test_schedulable_activity_is_not_reserved(self) -> None:
        reservations = reservations_for_excluded(
            date(2027, 1, 1), {self.planned.activity_id}
        )
        self.assertFalse(
            any(r.resource_id == "testa:OV-R1" for r in reservations),
            "A schedulable activity must not reserve its own resource.",
        )


class RescheduleProposalTest(TestCase):
    """Single-activity reschedule proposal when no swap resource exists."""

    def setUp(self) -> None:
        ConfigurazioneTesta.objects.create(
            modello_testa="TP60",
            min_mandrini=0,
            min_tastatori=0,
            min_maschere=0,
            min_generatori=0,
        )
        TestaItem.objects.create(uid="OV-T1", model="TP60", plant="Valbrembo")
        Job.objects.create(commessa="C205", delivery_date=date(2026, 9, 30))
        self.late = Attivita.objects.create(
            commessa="C101",
            item=1,
            activity="Saldatura",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 10),
            data_fine=date(2026, 8, 19),
            actual_start=date(2026, 8, 17),
        )
        self.blocked = Attivita.objects.create(
            commessa="C205",
            item=2,
            activity="Montaggio",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 20),
            data_fine=date(2026, 8, 29),
        )
        for att in (self.late, self.blocked):
            RisorsaAttivita.objects.create(
                attivita=att, tipo="testa", risorsa_id="OV-T1"
            )

    def test_propose_moves_activity_and_keeps_resource(self) -> None:
        proposal = propose_reschedule(self.blocked.pk)
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["status"], "proposed")
        self.assertEqual(proposal["blocked"]["resources"], ["OV-T1"])
        self.assertTrue(proposal["meets_deadline"])
        # The proposed window must not overlap the reserved late window.
        new_start = proposal["blocked"]["start"]
        self.assertNotEqual(new_start, self.blocked.data_inizio.isoformat())

    def _open_row(self):
        return ProblemaAttivita.objects.filter(
            attivita=self.blocked,
            tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
            risolto=False,
        ).first()

    def test_auto_resolve_does_not_presolve(self) -> None:
        """Loading a page must not spend the solver on unopened problems."""
        auto_resolve_resource_overlaps()
        row = self._open_row()
        self.assertIsNotNone(row)
        self.assertIsNone(row.proposta_json)

    def test_compute_stores_a_fresh_proposal(self) -> None:
        auto_resolve_resource_overlaps()
        payload = compute_reschedule_proposal(self.blocked.pk)
        self.assertEqual(payload["status"], "proposed")
        row = self._open_row()
        row.refresh_from_db()
        self.assertEqual(
            row.proposta_json["blocked"]["start"], payload["blocked"]["start"]
        )

    def test_compute_follows_the_current_plan(self) -> None:
        """A proposal is never reused: moving the blocker re-solves it."""
        auto_resolve_resource_overlaps()
        first = compute_reschedule_proposal(self.blocked.pk)
        # The blocker now holds OV-T1 well past the first proposed slot.
        self.late.data_fine = date(2026, 10, 15)
        self.late.save(update_fields=["data_fine"])
        second = compute_reschedule_proposal(self.blocked.pk)
        self.assertNotEqual(first["blocked"]["start"], second["blocked"]["start"])
        row = self._open_row()
        row.refresh_from_db()
        self.assertEqual(
            row.proposta_json["blocked"]["start"], second["blocked"]["start"]
        )

    def test_compute_keeps_a_rejected_proposal_dismissed(self) -> None:
        auto_resolve_resource_overlaps()
        compute_reschedule_proposal(self.blocked.pk)
        reject_reschedule(self.blocked.pk)
        self.assertEqual(
            compute_reschedule_proposal(self.blocked.pk),
            {"status": "rejected"},
        )

    def test_compute_can_skip_storing(self) -> None:
        """Read-only callers get the payload without writing it."""
        auto_resolve_resource_overlaps()
        payload = compute_reschedule_proposal(self.blocked.pk, store=False)
        self.assertEqual(payload["status"], "proposed")
        row = self._open_row()
        row.refresh_from_db()
        self.assertIsNone(row.proposta_json)

    def test_reject_marks_proposal_rejected(self) -> None:
        auto_resolve_resource_overlaps()
        compute_reschedule_proposal(self.blocked.pk)
        reject_reschedule(self.blocked.pk)
        row = ProblemaAttivita.objects.filter(
            attivita=self.blocked,
            tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
            risolto=False,
        ).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.proposta_json.get("status"), "rejected")

    def test_apply_reschedule_resolves_overlap(self) -> None:
        auto_resolve_resource_overlaps()
        compute_reschedule_proposal(self.blocked.pk)
        apply_reschedule(self.blocked.pk)
        self.blocked.refresh_from_db()
        self.assertNotEqual(self.blocked.data_inizio, date(2026, 8, 20))
        self.assertEqual(
            list(
                RisorsaAttivita.objects.filter(attivita=self.blocked).values_list(
                    "risorsa_id", flat=True
                )
            ),
            ["OV-T1"],
        )
        self.assertEqual(find_resource_overlaps(), {})


class OverdueRescheduleProposalTest(TestCase):
    """A late activity carries a proposal even without any overlap.

    Reproduces 25078-02-SNA/SNB: never started, past the planned finish and
    sharing no resource with anyone, so the only open row is ``ritardo``.
    The proposal must still be computed and exposed by the problems API,
    which is what the notes modal renders.
    """

    def setUp(self) -> None:
        ConfigurazioneTesta.objects.create(
            modello_testa="TP60",
            min_mandrini=0,
            min_tastatori=0,
            min_maschere=0,
            min_generatori=0,
        )
        TestaItem.objects.create(uid="OV-T9", model="TP60", plant="Valbrembo")
        Job.objects.create(
            commessa="C300", delivery_date=localdate() + timedelta(days=90)
        )
        self.late = Attivita.objects.create(
            commessa="C300",
            item=2,
            activity="SNA-produzione",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=localdate() - timedelta(days=20),
            data_fine=localdate() - timedelta(days=10),
        )
        RisorsaAttivita.objects.create(
            attivita=self.late, tipo="testa", risorsa_id="OV-T9"
        )

    def test_ritardo_row_carries_the_proposal(self) -> None:
        auto_resolve_resource_overlaps()
        compute_reschedule_proposal(self.late.pk)
        self.assertFalse(
            ProblemaAttivita.objects.filter(
                attivita=self.late,
                tipo=ProblemaAttivita.TIPO_SOVRAPPOSIZIONE,
            ).exists(),
            "No resource is shared: there must be no overlap row.",
        )
        row = ProblemaAttivita.objects.get(
            attivita=self.late,
            tipo=ProblemaAttivita.TIPO_RITARDO,
            risolto=False,
        )
        self.assertEqual(row.proposta_json.get("status"), "proposed")

    def test_proposal_endpoint_solves_for_a_ritardo_only_activity(self) -> None:
        auto_resolve_resource_overlaps()
        client = Client()
        client.force_login(write_user())
        resp = client.get(f"/api/attivita/{self.late.pk}/riprogramma/proposta/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["proposta"]["status"], "proposed")
        row = ProblemaAttivita.objects.get(
            attivita=self.late,
            tipo=ProblemaAttivita.TIPO_RITARDO,
            risolto=False,
        )
        self.assertEqual(row.proposta_json.get("status"), "proposed")


class RescheduleResourcesTest(TestCase):
    """Accepting a reschedule keeps the resource assignment."""

    def setUp(self) -> None:
        ConfigurazioneTesta.objects.create(
            modello_testa="TP60",
            min_mandrini=0,
            min_tastatori=0,
            min_maschere=0,
            min_generatori=0,
        )
        # A single TP60 head: no swap is possible, so the only way out is to
        # move the blocked activity, which is the flow under test.
        TestaItem.objects.create(uid="OV-T1", model="TP60", plant="Valbrembo")
        MandrinoItem.objects.create(uid="M1", diameter="20", plant="Valbrembo")
        GeneratoreItem.objects.create(uid="G1", model="GEN", plant="Valbrembo")
        Job.objects.create(commessa="C205", delivery_date=date(2026, 9, 30))

        self.late = Attivita.objects.create(
            commessa="C101",
            item=1,
            activity="Saldatura",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 10),
            data_fine=date(2026, 8, 19),
            actual_start=date(2026, 8, 17),
        )
        self.blocked = Attivita.objects.create(
            commessa="C205",
            item=2,
            activity="Montaggio",
            stabilimento="Valbrembo",
            modello_testa="TP60",
            data_inizio=date(2026, 8, 20),
            data_fine=date(2026, 8, 29),
        )
        RisorsaAttivita.objects.create(
            attivita=self.late, tipo="testa", risorsa_id="OV-T1"
        )
        for tipo, uid in (("testa", "OV-T1"), ("mandrino", "M1"), ("generatore", "G1")):
            RisorsaAttivita.objects.create(
                attivita=self.blocked, tipo=tipo, risorsa_id=uid
            )

    def test_accepting_keeps_the_resources(self) -> None:
        auto_resolve_resource_overlaps()
        self.assertEqual(
            compute_reschedule_proposal(self.blocked.pk).get("status"), "proposed"
        )

        apply_reschedule(self.blocked.pk)

        resources = RisorsaAttivita.objects.filter(attivita=self.blocked)
        self.assertEqual(
            sorted(resources.values_list("risorsa_id", flat=True)),
            ["G1", "M1", "OV-T1"],
        )
