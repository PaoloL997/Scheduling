"""Format activity resource assignments for compact UI display."""

from __future__ import annotations

from typing import Iterable

from ui.services.resource_types import TIPI, TIPO_LABELS as _TIPO_LABELS


def _group_by_tipo(risorse: Iterable) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {tipo: [] for tipo in TIPI}
    for r in risorse:
        tipo = getattr(r, "tipo", None) or r.get("tipo")  # type: ignore[union-attr]
        rid = getattr(r, "risorsa_id", None) or r.get("risorsa_id")  # type: ignore[union-attr]
        if tipo in grouped and rid:
            grouped[tipo].append(rid)
    return grouped


def format_activity_resources(risorse: Iterable) -> dict:
    """Build compact display and tooltip text for activity resources.

    Args:
        risorse: Iterable of :class:`~ui.models.RisorsaAttivita` instances or
            dicts with ``tipo`` and ``risorsa_id`` keys.

    Returns:
        dict: Keys ``display``, ``tooltip``, ``flat_ids``, and ``by_tipo``.
    """
    grouped = _group_by_tipo(risorse)
    teste = grouped["testa"]
    generatori = grouped["generatore"]
    accessories = grouped["mandrino"] + grouped["tastatore"] + grouped["maschera"]
    n_accessories = len(accessories)

    main_parts: list[str] = []
    if teste:
        main_parts.append(" · ".join(teste))
    if generatori:
        main_parts.append(" · ".join(generatori))

    display_parts: list[str] = []
    if main_parts:
        display_parts.append(" · ".join(main_parts))
    if n_accessories:
        label = "accessorio" if n_accessories == 1 else "accessori"
        display_parts.append(f"+ {n_accessories} {label}")

    display = " ".join(display_parts) if display_parts else "—"

    tooltip_lines: list[str] = []
    for tipo in ("testa", "generatore", "mandrino", "tastatore", "maschera"):
        for rid in grouped[tipo]:
            tooltip_lines.append(f"{_TIPO_LABELS[tipo]}: {rid}")
    tooltip = "\n".join(tooltip_lines)

    flat_ids = (
        teste
        + generatori
        + grouped["mandrino"]
        + grouped["tastatore"]
        + grouped["maschera"]
    )

    return {
        "display": display,
        "tooltip": tooltip,
        "flat_ids": flat_ids,
        "by_tipo": grouped,
    }


def format_activity_resources_html(risorse: Iterable) -> str:
    """Return HTML for the compact resources cell."""
    from django.utils.html import escape

    fmt = format_activity_resources(risorse)
    if not fmt["flat_ids"]:
        return "—"

    parts: list[str] = []
    grouped = fmt["by_tipo"]
    for tipo, css in (
        ("testa", "pian-res-chip--testa"),
        ("generatore", "pian-res-chip--gen"),
    ):
        for rid in grouped[tipo]:
            parts.append(f'<span class="pian-res-chip {css}">{escape(rid)}</span>')

    # Accessories are shown in full too, one chip each.
    for tipo, label in (
        ("mandrino", "Mandrino"),
        ("tastatore", "Tastatore"),
        ("maschera", "Maschera"),
    ):
        for rid in grouped[tipo]:
            parts.append(
                f'<span class="pian-res-chip pian-res-chip--acc" title="{label}">'
                f"{escape(rid)}</span>"
            )

    tooltip_attr = ""
    if fmt["tooltip"]:
        tooltip_attr = (
            f' data-tooltip="{escape(fmt["tooltip"])}" title="{escape(fmt["tooltip"])}"'
        )

    inner = '<span class="pian-res-inner">' + " ".join(parts) + "</span>"
    return f'<span class="pian-res-summary"{tooltip_attr}>{inner}</span>'
