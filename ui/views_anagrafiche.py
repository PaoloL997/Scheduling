"""One create/update/delete handler for the reference tables of Impostazioni.

Stabilimenti, responsabili, tipi attività and configurazioni testa are small
lookup tables with the same API: ``POST`` creates a row from a JSON body,
``PUT`` updates it, ``DELETE`` removes it, every change is written to the
operations log.  The only differences are the model, the name field, the
extra fields and the Italian wording, so they are data
(:class:`ReferenceTable`) and the handlers are built once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ui.permissions import write_required
from ui.services.parsing import parse_json_body


@dataclass(frozen=True)
class Field:
    """An optional column of a reference table.

    ``parse(raw, default)`` turns the request value into the stored value;
    *default* is ``create_default`` on creation and the current value on
    update, so an absent or invalid value never clears a column.
    """

    name: str
    parse: Callable[[Any, Any], Any]
    create_default: Any


@dataclass(frozen=True)
class ReferenceTable:
    """Everything that differs between two reference tables."""

    model: type
    name_field: str
    #: Lower-case noun for log lines: ``"stabilimento"``.
    label: str
    #: Capitalised noun for error messages: ``"Stabilimento"``.
    label_cap: str
    feminine: bool
    #: Prefix of the log ``riferimento`` (``"stabilimento:<pk>"``).
    riferimento: str
    fields: tuple[Field, ...] = ()
    #: ``PUT`` may rename the row (false for configurations, whose model is
    #: the identity the activities point at).
    name_editable: bool = True
    #: ``{value}`` is the name that already exists.
    duplicate_message: str = "{label_cap} '{value}' già esistente."

    def past(self, stem: str) -> str:
        """``Creato`` / ``Creata`` from the stem ``Creat``."""
        return stem + ("a" if self.feminine else "o")

    def serialize(self, obj) -> dict:
        payload = {"id": obj.pk, self.name_field: getattr(obj, self.name_field)}
        for field in self.fields:
            payload[field.name] = getattr(obj, field.name)
        return payload


def _text(raw: Any, default: Any) -> str:
    return (raw or default).strip()


def _non_negative_int(raw: Any, default: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _log(request: HttpRequest, azione: str, descrizione: str, riferimento: str) -> None:
    from ui.services.operation_log import record_operation

    record_operation(
        request, azione=azione, descrizione=descrizione, riferimento=riferimento
    )


def _required(table: ReferenceTable) -> JsonResponse:
    return JsonResponse(
        {"ok": False, "error": f"Il campo '{table.name_field}' è obbligatorio."},
        status=400,
    )


def _duplicate(table: ReferenceTable, value: str) -> JsonResponse:
    message = table.duplicate_message.format(label_cap=table.label_cap, value=value)
    return JsonResponse({"ok": False, "error": message}, status=409)


def create_view(table: ReferenceTable):
    """Build the ``POST`` view that creates a row of *table*."""

    @write_required
    @csrf_exempt
    @require_http_methods(["POST"])
    def view(request: HttpRequest) -> JsonResponse:
        data = parse_json_body(request)
        if isinstance(data, JsonResponse):
            return data

        value = (data.get(table.name_field) or "").strip()
        if not value:
            return _required(table)
        if table.model.objects.filter(
            **{f"{table.name_field}__iexact": value}
        ).exists():
            return _duplicate(table, value)

        values = {table.name_field: value}
        for field in table.fields:
            values[field.name] = field.parse(data.get(field.name), field.create_default)
        obj = table.model.objects.create(**values)

        _log(
            request,
            "anagrafica_crea",
            f"{table.past('Creat')} {table.label} {value}",
            f"{table.riferimento}:{obj.pk}",
        )
        return JsonResponse({"ok": True, **table.serialize(obj)}, status=201)

    view.__name__ = f"api_{table.riferimento}_create"
    return view


def detail_view(table: ReferenceTable):
    """Build the ``PUT`` / ``DELETE`` view on one row of *table*."""

    @write_required
    @csrf_exempt
    @require_http_methods(["PUT", "DELETE"])
    def view(request: HttpRequest, pk: int) -> JsonResponse:
        try:
            obj = table.model.objects.get(pk=pk)
        except table.model.DoesNotExist:
            return JsonResponse(
                {
                    "ok": False,
                    "error": f"{table.label_cap} non {table.past('trovat')}.",
                },
                status=404,
            )

        if request.method == "DELETE":
            name = getattr(obj, table.name_field)
            obj.delete()
            _log(
                request,
                "anagrafica_elimina",
                f"{table.past('Eliminat')} {table.label} {name}",
                f"{table.riferimento}:{pk}",
            )
            return JsonResponse({"ok": True})

        data = parse_json_body(request)
        if isinstance(data, JsonResponse):
            return data

        if table.name_editable:
            value = (data.get(table.name_field) or "").strip()
            if not value:
                return _required(table)
            if (
                table.model.objects.filter(**{f"{table.name_field}__iexact": value})
                .exclude(pk=pk)
                .exists()
            ):
                return _duplicate(table, value)
            setattr(obj, table.name_field, value)
        for field in table.fields:
            setattr(
                obj,
                field.name,
                field.parse(data.get(field.name), getattr(obj, field.name)),
            )
        obj.save()

        _log(
            request,
            "anagrafica_modifica",
            f"{table.past('Aggiornat')} {table.label} {getattr(obj, table.name_field)}",
            f"{table.riferimento}:{obj.pk}",
        )
        return JsonResponse({"ok": True, **table.serialize(obj)})

    view.__name__ = f"api_{table.riferimento}_detail"
    return view


def _tables() -> dict[str, ReferenceTable]:
    from ui.models import ConfigurazioneTesta, Responsabile, Stabilimento, TipoAttivita

    return {
        "stabilimento": ReferenceTable(
            model=Stabilimento,
            name_field="nome",
            label="stabilimento",
            label_cap="Stabilimento",
            feminine=False,
            riferimento="stabilimento",
            fields=(Field("colore", _text, "#6366f1"),),
        ),
        "responsabile": ReferenceTable(
            model=Responsabile,
            name_field="nome",
            label="responsabile",
            label_cap="Responsabile",
            feminine=False,
            riferimento="responsabile",
        ),
        "tipo_attivita": ReferenceTable(
            model=TipoAttivita,
            name_field="nome",
            label="tipo attività",
            label_cap="Tipo attività",
            feminine=False,
            riferimento="tipo_attivita",
        ),
        "config_testa": ReferenceTable(
            model=ConfigurazioneTesta,
            name_field="modello_testa",
            label="configurazione testa",
            label_cap="Configurazione",
            feminine=True,
            riferimento="config_testa",
            fields=tuple(
                Field(name, _non_negative_int, 1)
                for name in (
                    "min_mandrini",
                    "min_tastatori",
                    "min_maschere",
                    "min_generatori",
                )
            ),
            name_editable=False,
            duplicate_message="Configurazione per '{value}' già esistente.",
        ),
    }


TABLES = _tables()
