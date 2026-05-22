"""Management command to seed the inventory tables with deterministic test data.

This command provides a predictable resource pool that allows manually testing
the add-activity wizard and the resource conflict prevention rules without
relying on an external Microsoft Project import.  It is idempotent: re-running
it updates existing rows in place rather than creating duplicates.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from ui.models import (
    GeneratoreItem,
    MandrinoItem,
    MascheraItem,
    Stabilimento,
    TastatoreItem,
    TestaItem,
)


_PLANT_NAME = "Stabilimento Test"
_PLANT_COLOR = "#6366f1"


class Command(BaseCommand):
    """Populate inventory tables with a small, fixed set of test resources.

    Creates ``Stabilimento Test`` and two units for each resource category,
    using the canonical naming conventions (Mandrino -> Cx, Tastatore -> Tx,
    Maschera -> Mx, Testa -> tx, Generatore -> gx).  Teste use the ``TP60``
    model and generators use the ``P6`` model.

    Use ``python manage.py seed_test_inventory`` to apply.
    """

    help = "Seed the inventory tables with deterministic test resources."

    def handle(self, *args, **options) -> None:
        """Run the seeding routine inside a single atomic transaction.

        Returns:
            None: Writes inventory rows to the database and prints a summary.
        """
        with transaction.atomic():
            self._seed_stabilimento()
            self._seed_teste()
            self._seed_mandrini()
            self._seed_tastatori()
            self._seed_maschere()
            self._seed_generatori()

        self.stdout.write(
            self.style.SUCCESS(f"Inventory seed completato per '{_PLANT_NAME}'.")
        )

    def _seed_stabilimento(self) -> None:
        """Ensure the test plant exists in the Stabilimento table.

        Returns:
            None: Creates or updates ``Stabilimento Test`` with the default colour.
        """
        Stabilimento.objects.update_or_create(
            nome=_PLANT_NAME,
            defaults={"colore": _PLANT_COLOR},
        )

    def _seed_teste(self) -> None:
        """Seed two TestaItem rows with model ``TP60`` at the test plant.

        Returns:
            None: Upserts ``t1`` and ``t2``.
        """
        for uid in ("t1", "t2"):
            TestaItem.objects.update_or_create(
                uid=uid,
                defaults={
                    "model": "TP60",
                    "plant": _PLANT_NAME,
                    "notes": "seed",
                },
            )

    def _seed_mandrini(self) -> None:
        """Seed two MandrinoItem rows with diameter ``100``.

        Returns:
            None: Upserts ``C1`` and ``C2``.
        """
        for uid in ("C1", "C2"):
            MandrinoItem.objects.update_or_create(
                uid=uid,
                defaults={
                    "diameter": "100",
                    "plant": _PLANT_NAME,
                    "notes": "seed",
                },
            )

    def _seed_tastatori(self) -> None:
        """Seed two TastatoreItem rows with diameter ``100``.

        Returns:
            None: Upserts ``T1`` and ``T2``.
        """
        for uid in ("T1", "T2"):
            TastatoreItem.objects.update_or_create(
                uid=uid,
                defaults={
                    "diameter": "100",
                    "plant": _PLANT_NAME,
                    "notes": "seed",
                },
            )

    def _seed_maschere(self) -> None:
        """Seed two MascheraItem rows with pitch ``10``.

        Returns:
            None: Upserts ``M1`` and ``M2``.
        """
        for uid in ("M1", "M2"):
            MascheraItem.objects.update_or_create(
                uid=uid,
                defaults={
                    "pitch": "10",
                    "plant": _PLANT_NAME,
                    "center_distance": "",
                },
            )

    def _seed_generatori(self) -> None:
        """Seed two GeneratoreItem rows with model ``P6``.

        Returns:
            None: Upserts ``g1`` and ``g2``.
        """
        for uid in ("g1", "g2"):
            GeneratoreItem.objects.update_or_create(
                uid=uid,
                defaults={
                    "model": "P6",
                    "plant": _PLANT_NAME,
                    "notes": "seed",
                },
            )
