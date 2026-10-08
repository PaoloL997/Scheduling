"""The five resource categories, in their three spellings, in one place.

The same five physical resource types are named three ways across the
application:

* ``tipo`` — the value stored on assignments and problems
  (``testa``, ``mandrino``, ``tastatore``, ``maschera``, ``generatore``);
* ``categoria`` — the wizard / API key of the list of selected UIDs
  (``teste``, ``mandrini``, ``tastatori``, ``maschere``, ``generatori``);
* ``table`` — the inventory table shown on the Inventario page
  (``Testa``, ``Mandrino``, ``Tastatore``, ``Maschera``, ``Generatore``).

Every mapping between them, the Italian labels and the inventory model of
each type are defined here and nowhere else.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Assignment ``tipo`` values, in display order.
TIPI: tuple[str, ...] = ("testa", "mandrino", "tastatore", "maschera", "generatore")

#: Wizard / API ``categoria`` keys, in the same order as :data:`TIPI`.
CATEGORIE: tuple[str, ...] = (
    "teste",
    "mandrini",
    "tastatori",
    "maschere",
    "generatori",
)

#: Inventario table names, in the same order as :data:`TIPI`.
TABLES: tuple[str, ...] = ("Testa", "Mandrino", "Tastatore", "Maschera", "Generatore")

TIPO_LABELS: dict[str, str] = dict(zip(TIPI, TABLES))
TIPO_WITH_ARTICLE: dict[str, str] = {
    "testa": "la testa",
    "mandrino": "il mandrino",
    "tastatore": "il tastatore",
    "maschera": "la maschera",
    "generatore": "il generatore",
}

CATEGORIA_TO_TIPO: dict[str, str] = dict(zip(CATEGORIE, TIPI))
TIPO_TO_CATEGORIA: dict[str, str] = dict(zip(TIPI, CATEGORIE))
TABLE_TO_TIPO: dict[str, str] = dict(zip(TABLES, TIPI))
TIPO_TO_TABLE: dict[str, str] = dict(zip(TIPI, TABLES))

#: Colour of a plant that has no :class:`~ui.models.Stabilimento` row.
DEFAULT_PLANT_COLOR = "#4a90e2"


def _models() -> dict[str, type]:
    from ui.models import (
        GeneratoreItem,
        MandrinoItem,
        MascheraItem,
        TastatoreItem,
        TestaItem,
    )

    return {
        "testa": TestaItem,
        "mandrino": MandrinoItem,
        "tastatore": TastatoreItem,
        "maschera": MascheraItem,
        "generatore": GeneratoreItem,
    }


def model_for_tipo(tipo: str) -> type:
    """Return the inventory model of a ``tipo`` (``KeyError`` when unknown)."""
    return _models()[tipo]


def model_for_table(table: str) -> type:
    """Return the inventory model of an Inventario table name."""
    return _models()[TABLE_TO_TIPO[table]]


def risorse_dict_to_pairs(risorse: dict | None) -> list[tuple[str, str]]:
    """Flatten a wizard-style ``{categoria: [uid, ...]}`` into ``(tipo, uid)`` pairs.

    Categories are visited in :data:`CATEGORIE` order; blank UIDs are skipped
    and every UID is stringified.
    """
    pairs: list[tuple[str, str]] = []
    for categoria, tipo in CATEGORIA_TO_TIPO.items():
        for uid in (risorse or {}).get(categoria) or []:
            if uid:
                pairs.append((tipo, str(uid)))
    return pairs


def pairs_to_risorse_dict(pairs: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    """Group ``(tipo, uid)`` pairs into a wizard-style dict with every category present."""
    risorse: dict[str, list[str]] = {categoria: [] for categoria in CATEGORIE}
    for tipo, uid in pairs:
        categoria = TIPO_TO_CATEGORIA.get(tipo)
        if categoria and uid:
            risorse[categoria].append(uid)
    return risorse
