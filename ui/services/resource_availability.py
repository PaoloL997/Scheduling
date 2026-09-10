"""Today's availability of an inventory item, for the inventory page.

An item is *occupata* when an activity occupancy window
(:func:`ui.services.resource_conflicts.occupation_window`) contains today, and
*libera* otherwise.  Occupancy is never re-implemented here: the bookings come
from :func:`ui.services.resource_conflicts.bookings_not_ended`.

Both states carry the context the inventory tooltip shows: an occupied item
says which activities hold it and when it is released, a free one says how long
it stays free and which activities take it next.  Back-to-back bookings are
followed, so the release day is the first day the item is really idle.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from django.utils.html import escape

from ui.services.resource_conflicts import bookings_not_ended

TIPO_BY_TABLE: dict[str, str] = {
    "Generatore": "generatore",
    "Mandrino": "mandrino",
    "Maschera": "maschera",
    "Tastatore": "tastatore",
    "Testa": "testa",
}

# Activities listed in a tooltip before it is summarised with a "+ N" line.
_MAX_TOOLTIP_ACTIVITIES = 3

_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class _Occupazione:
    """One activity holding a resource over an inclusive day range."""

    attivita_id: int
    label: str
    inizio: date
    fine: date

    def as_dict(self) -> dict:
        """Return the JSON-serialisable form used by the template and the API."""
        return {
            "attivita_id": self.attivita_id,
            "label": self.label,
            "data_inizio": self.inizio.isoformat(),
            "data_fine": self.fine.isoformat(),
        }


def _format_day(day: date) -> str:
    """Return an Italian ``dd/mm/yyyy`` date."""
    return day.strftime("%d/%m/%Y")


def _format_range(occupazione: _Occupazione) -> str:
    """Return the ``dd/mm/yyyy – dd/mm/yyyy`` window of an occupation."""
    return f"{_format_day(occupazione.inizio)} – {_format_day(occupazione.fine)}"


def _activity_label(attivita) -> str:
    """Return ``commessa-item-activity`` as typed, not the URL-encoded id."""
    return f"{attivita.commessa}-{attivita.item}-{attivita.activity}"


def _activity_lines(occupazioni: list[_Occupazione]) -> list[str]:
    """Return one bullet per activity, truncated to keep the tooltip short."""
    shown = occupazioni[:_MAX_TOOLTIP_ACTIVITIES]
    lines = [f"• {o.label} ({_format_range(o)})" for o in shown]
    remaining = len(occupazioni) - len(shown)
    if remaining > 0:
        label = "altra attività" if remaining == 1 else "altre attività"
        lines.append(f"• + {remaining} {label}")
    return lines


def _release_day(occupazioni: list[_Occupazione], today: date) -> date:
    """Return the first day the resource is free, following back-to-back holds.

    Args:
        occupazioni: Every occupation that has not ended before *today*.
        today: Reference day; at least one occupation must contain it.

    Returns:
        date: The day after the last day of the uninterrupted chain of
            occupations that starts on or before *today*.
    """
    end = max(o.fine for o in occupazioni if o.inizio <= today)
    extended = True
    while extended:
        extended = False
        for o in occupazioni:
            if o.inizio <= end + _ONE_DAY and o.fine > end:
                end = o.fine
                extended = True
    return end + _ONE_DAY


def _build_info(occupazioni: list[_Occupazione], today: date) -> dict:
    """Assemble the availability descriptor for a single resource."""
    ordered = sorted(occupazioni, key=lambda o: (o.inizio, o.fine, o.label))

    if any(o.inizio <= today for o in ordered):
        libera_dal = _release_day(ordered, today)
        catena = [o for o in ordered if o.inizio < libera_dal]
        giorni = (libera_dal - today).days
        quando = "domani" if giorni <= 1 else f"fra {giorni} giorni"
        tooltip = "\n".join(
            ["Occupata oggi da:", *_activity_lines(catena)]
            + [f"Si libera {quando} ({_format_day(libera_dal)})."]
        )
        return {
            "stato": "occupata",
            "libera": False,
            "etichetta": "Occupata",
            "occupata_fino": (libera_dal - _ONE_DAY).isoformat(),
            "libera_dal": libera_dal.isoformat(),
            "prossimo_impegno": None,
            "giorni": giorni,
            "attivita": [o.as_dict() for o in catena],
            "tooltip": tooltip,
        }

    if not ordered:
        return {
            "stato": "libera",
            "libera": True,
            "etichetta": "Libera",
            "occupata_fino": None,
            "libera_dal": None,
            "prossimo_impegno": None,
            "giorni": None,
            "attivita": [],
            "tooltip": "Libera oggi.\nNessun impegno pianificato.",
        }

    prossimo = ordered[0].inizio
    giorni = (prossimo - today).days
    quando = (
        f"Impegnata da domani ({_format_day(prossimo)}):"
        if giorni <= 1
        else f"Impegnata fra {giorni} giorni, dal {_format_day(prossimo)}:"
    )
    tooltip = "\n".join(["Libera oggi.", quando, *_activity_lines(ordered)])
    return {
        "stato": "libera",
        "libera": True,
        "etichetta": "Libera",
        "occupata_fino": None,
        "libera_dal": None,
        "prossimo_impegno": prossimo.isoformat(),
        "giorni": giorni,
        "attivita": [o.as_dict() for o in ordered],
        "tooltip": tooltip,
    }


def availability_map(
    tipo: str,
    risorsa_ids: Iterable[str],
    today: date | None = None,
) -> dict[str, dict]:
    """Return the availability descriptor of every requested resource.

    Args:
        tipo: Resource category (``testa``, ``mandrino``, ``tastatore``,
            ``maschera``, ``generatore``).
        risorsa_ids: Inventory UIDs to describe.
        today: Reference day; defaults to the current date.

    Returns:
        dict[str, dict]: One descriptor per UID with keys ``stato``, ``libera``,
            ``etichetta``, ``occupata_fino``, ``libera_dal``,
            ``prossimo_impegno``, ``giorni``, ``attivita`` and ``tooltip``.
    """
    day = today or date.today()
    per_uid: dict[str, list[_Occupazione]] = {
        uid: [] for uid in risorsa_ids if uid is not None
    }
    for row, occ_start, occ_end in bookings_not_ended(tipo, per_uid.keys(), day):
        bucket = per_uid.get(row.risorsa_id)
        if bucket is None:
            continue
        bucket.append(
            _Occupazione(
                attivita_id=row.attivita_id,
                label=_activity_label(row.attivita),
                inizio=occ_start,
                fine=occ_end,
            )
        )
    return {uid: _build_info(occupazioni, day) for uid, occupazioni in per_uid.items()}


def availability_cell_html(info: dict) -> str:
    """Return the coloured dot markup for one inventory row.

    Args:
        info: A descriptor as returned by :func:`availability_map`.

    Returns:
        str: A ``<span>`` carrying the state class and the hover tooltip.
            The tooltip text doubles as the accessible name; no ``title`` is
            set, so the native bubble never doubles up with the popover.
    """
    tooltip = escape(info["tooltip"])
    return (
        f'<span class="inv-avail inv-avail--{info["stato"]}" tabindex="0" '
        f'role="img" aria-label="{tooltip}" data-tooltip="{tooltip}">'
        f'<span class="inv-avail-dot"></span></span>'
    )


def annotate_inventory_rows(
    table: str,
    rows: list[dict],
    today: date | None = None,
) -> list[dict]:
    """Add ``disponibilita`` and ``disponibilita_html`` to inventory rows.

    Args:
        table: Inventory table name (``Generatore``, ``Mandrino``,
            ``Maschera``, ``Tastatore``, ``Testa``).
        rows: Row dicts as returned by the inventory queries; each must carry
            a ``uid`` key.  Mutated in place.
        today: Reference day; defaults to the current date.

    Returns:
        list[dict]: The same ``rows`` list, for convenient chaining.
    """
    day = today or date.today()
    tipo = TIPO_BY_TABLE.get(table)
    infos = availability_map(tipo, [r.get("uid") for r in rows], day) if tipo else {}
    for row in rows:
        info = infos.get(row.get("uid")) or _build_info([], day)
        row["disponibilita"] = info
        row["disponibilita_html"] = availability_cell_html(info)
    return rows
