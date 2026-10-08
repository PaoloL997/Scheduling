"""Reconcile every operational note from a scheduler (e.g. nightly)."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from ui.models import ProblemaAttivita
from ui.services.reconcile import reconcile


class Command(BaseCommand):
    help = (
        "Riallinea le note operative (acquisto, spostamento, sovrapposizione, "
        "ritardo) ai dati correnti. Da schedulare una volta al giorno: il "
        "ritardo dipende dal calendario e non da una scrittura."
    )

    def handle(self, *args, **options):
        open_before = ProblemaAttivita.objects.filter(risolto=False).count()
        result = reconcile()
        open_after = ProblemaAttivita.objects.filter(risolto=False).count()
        self.stdout.write(
            f"Problemi aperti: {open_before} -> {open_after}; "
            f"sostituzioni automatiche: {len(result.get('swapped_blocked_ids', []))}."
        )
