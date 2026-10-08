"""ORM → JSON serializers for the tablet bridge API."""

from __future__ import annotations

from ui.models import (
    ConfigurazioneTesta,
    GeneratoreItem,
    MandrinoItem,
    MascheraItem,
    ProblemaAttivita,
    Responsabile,
    Stabilimento,
    TastatoreItem,
    TestaItem,
)
from ui.services.runtime_config import load_runtime_config
from ui.services.planning_read import fetch_jobs, fetch_tasks, load_task_dict


def serialize_attivita_list(stabilimento: str | None = None) -> list[dict]:
    """Return activities as planning row dicts, optionally filtered by plant."""
    return fetch_tasks(stabilimento)


def serialize_attivita(pk: int) -> dict | None:
    """Return a single activity dict, or ``None`` if not found."""
    return load_task_dict(pk)


def serialize_jobs() -> list[dict]:
    """Return all commesse with activity counts."""
    return fetch_jobs()


def _inventory_rows(table: str, model, fields: list[str]) -> list[dict]:
    """Return one inventory table, each row carrying today's availability.

    The descriptor is the inventory page's own
    (:func:`ui.services.resource_availability.annotate_inventory_rows`), computed
    over the activities of every plant.  The dataset filters activities by the
    user's plant, so the tablet cannot derive availability from them.
    """
    from ui.services.resource_availability import annotate_inventory_rows

    rows = [dict(row) for row in model.objects.order_by("uid").values(*fields)]
    annotate_inventory_rows(table, rows)
    for row in rows:
        row.pop("disponibilita_html", None)
    return rows


def serialize_inventario() -> dict[str, list[dict]]:
    """Return all five inventory tables in one payload."""
    return {
        "Testa": _inventory_rows(
            "Testa", TestaItem, ["uid", "model", "plant", "notes", "in_manutenzione"]
        ),
        "Mandrino": _inventory_rows(
            "Mandrino", MandrinoItem, ["uid", "diameter", "plant", "notes"]
        ),
        "Tastatore": _inventory_rows(
            "Tastatore", TastatoreItem, ["uid", "diameter", "plant", "notes"]
        ),
        "Maschera": _inventory_rows(
            "Maschera", MascheraItem, ["uid", "pitch", "plant", "center_distance"]
        ),
        "Generatore": _inventory_rows(
            "Generatore",
            GeneratoreItem,
            ["uid", "model", "plant", "notes", "in_manutenzione"],
        ),
    }


def serialize_stabilimenti() -> list[dict]:
    return list(Stabilimento.objects.order_by("nome").values("id", "nome", "colore"))


def serialize_responsabili() -> list[dict]:
    return list(Responsabile.objects.order_by("nome").values("id", "nome"))


def serialize_tipi_attivita() -> list[dict]:
    from ui.models import TipoAttivita

    return list(TipoAttivita.objects.order_by("nome").values("id", "nome"))


def serialize_problemi() -> list[dict]:
    """Return all problems with activity reference fields."""
    rows = (
        ProblemaAttivita.objects.select_related("attivita")
        .order_by("created_at")
        .values(
            "id",
            "attivita_id",
            "tipo",
            "risorsa_tipo",
            "risorsa_uid",
            "stabilimento_origine",
            "descrizione",
            "risolto",
            "note_risoluzione",
            "data_risoluzione",
            "created_at",
            "attivita__commessa",
            "attivita__item",
            "attivita__activity",
            "attivita__activity_id",
            "attivita__stabilimento",
        )
    )
    result = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "attivita_id": row["attivita_id"],
                "activity_id": row["attivita__activity_id"],
                "commessa": row["attivita__commessa"],
                "item": row["attivita__item"],
                "activity": row["attivita__activity"],
                "stabilimento_attivita": row["attivita__stabilimento"] or "",
                "tipo": row["tipo"],
                "risorsa_tipo": row["risorsa_tipo"],
                "risorsa_uid": row["risorsa_uid"],
                "stabilimento_origine": row["stabilimento_origine"],
                "descrizione": row["descrizione"],
                "risolto": row["risolto"],
                "note_risoluzione": row["note_risoluzione"],
                "data_risoluzione": row["data_risoluzione"].isoformat()
                if row["data_risoluzione"]
                else None,
                "created_at": row["created_at"].isoformat()
                if row["created_at"]
                else None,
            }
        )
    return result


def serialize_app_settings() -> dict:
    cfg = load_runtime_config()
    return {
        "penale_max": cfg.penale_max,
        "solver_time_limit": cfg.solver_time_limit,
        "openai_wps_model": cfg.openai_wps_model,
    }


def serialize_config_testa() -> list[dict]:
    configs = ConfigurazioneTesta.objects.prefetch_related(
        "generatori_compatibili"
    ).order_by("modello_testa")
    result = []
    for cfg in configs:
        result.append(
            {
                "id": cfg.pk,
                "modello_testa": cfg.modello_testa,
                "min_mandrini": cfg.min_mandrini,
                "min_tastatori": cfg.min_tastatori,
                "min_maschere": cfg.min_maschere,
                "min_generatori": cfg.min_generatori,
                "generatori_compatibili": [
                    {"id": g.pk, "modello_generatore": g.modello_generatore}
                    for g in cfg.generatori_compatibili.all()
                ],
            }
        )
    return result


def serialize_dataset(stabilimento: str | None = None) -> dict:
    """Full dataset snapshot for offline-friendly tablet sync."""
    return {
        "attivita": serialize_attivita_list(stabilimento),
        "jobs": serialize_jobs(),
        "inventario": serialize_inventario(),
        "stabilimenti": serialize_stabilimenti(),
        "responsabili": serialize_responsabili(),
        "tipi_attivita": serialize_tipi_attivita(),
        "problemi": serialize_problemi(),
        "app_settings": serialize_app_settings(),
        "config_testa": serialize_config_testa(),
    }
