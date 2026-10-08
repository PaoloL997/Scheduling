"""The reference tables share one handler; wording and shapes stay per table."""

from __future__ import annotations

import json

from django.test import Client, TestCase

from ui.models import (
    ConfigurazioneTesta,
    OperationLog,
    Responsabile,
    Stabilimento,
    TipoAttivita,
)
from ui.tests.helpers import create_writer


class _Base(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.client.force_login(create_writer("anagrafiche"))

    def post(self, url, body):
        return self.client.post(
            url, data=json.dumps(body), content_type="application/json"
        )

    def put(self, url, body):
        return self.client.put(
            url, data=json.dumps(body), content_type="application/json"
        )

    def last_log(self, azione):
        return OperationLog.objects.filter(azione=azione).latest("pk")


class StabilimentiApiTest(_Base):
    def test_create_update_delete(self) -> None:
        resp = self.post("/api/stabilimenti/", {"nome": "Schio"})
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body["nome"], "Schio")
        self.assertEqual(body["colore"], "#6366f1")
        pk = body["id"]
        self.assertEqual(
            self.last_log("anagrafica_crea").descrizione, "Creato stabilimento Schio"
        )
        self.assertEqual(
            self.last_log("anagrafica_crea").riferimento, f"stabilimento:{pk}"
        )

        resp = self.put(
            f"/api/stabilimenti/{pk}/", {"nome": "Schio Nord", "colore": " #112233 "}
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(
            resp.json(),
            {"ok": True, "id": pk, "nome": "Schio Nord", "colore": "#112233"},
        )
        self.assertEqual(
            self.last_log("anagrafica_modifica").descrizione,
            "Aggiornato stabilimento Schio Nord",
        )
        # Colour absent from the body keeps the stored one.
        self.put(f"/api/stabilimenti/{pk}/", {"nome": "Schio Nord"})
        self.assertEqual(Stabilimento.objects.get(pk=pk).colore, "#112233")

        resp = self.client.delete(f"/api/stabilimenti/{pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Stabilimento.objects.filter(pk=pk).exists())
        self.assertEqual(
            self.last_log("anagrafica_elimina").descrizione,
            "Eliminato stabilimento Schio Nord",
        )

    def test_validation_wording(self) -> None:
        Stabilimento.objects.create(nome="Ricengo")
        resp = self.post("/api/stabilimenti/", {"nome": " ricengo "})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "Stabilimento 'ricengo' già esistente.")
        resp = self.post("/api/stabilimenti/", {"nome": ""})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Il campo 'nome' è obbligatorio.")
        resp = self.put("/api/stabilimenti/9999/", {"nome": "x"})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"], "Stabilimento non trovato.")
        resp = self.post("/api/stabilimenti/", "{nope")
        self.assertEqual(resp.status_code, 400)


class ResponsabiliAndTipiApiTest(_Base):
    def test_responsabili(self) -> None:
        resp = self.post("/api/responsabili/", {"nome": "Rossi"})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(
            resp.json(), {"ok": True, "id": resp.json()["id"], "nome": "Rossi"}
        )
        pk = resp.json()["id"]
        resp = self.put(f"/api/responsabili/{pk}/", {"nome": "Bianchi"})
        self.assertEqual(Responsabile.objects.get(pk=pk).nome, "Bianchi")
        resp = self.post("/api/responsabili/", {"nome": "bianchi"})
        self.assertEqual(resp.json()["error"], "Responsabile 'bianchi' già esistente.")
        resp = self.client.delete("/api/responsabili/9999/")
        self.assertEqual(resp.json()["error"], "Responsabile non trovato.")

    def test_tipi_attivita(self) -> None:
        resp = self.post("/api/tipi-attivita/", {"nome": "Taglio"})
        self.assertEqual(resp.status_code, 201)
        pk = resp.json()["id"]
        self.assertEqual(
            self.last_log("anagrafica_crea").descrizione, "Creato tipo attività Taglio"
        )
        self.assertEqual(
            self.last_log("anagrafica_crea").riferimento, f"tipo_attivita:{pk}"
        )
        resp = self.post("/api/tipi-attivita/", {"nome": "TAGLIO"})
        self.assertEqual(resp.json()["error"], "Tipo attività 'TAGLIO' già esistente.")
        resp = self.put("/api/tipi-attivita/9999/", {"nome": "x"})
        self.assertEqual(resp.json()["error"], "Tipo attività non trovato.")
        self.client.delete(f"/api/tipi-attivita/{pk}/")
        self.assertFalse(TipoAttivita.objects.filter(pk=pk).exists())


class ConfigTestaApiTest(_Base):
    def test_create_defaults_and_update_keeps_the_model(self) -> None:
        resp = self.post(
            "/api/config-testa/",
            {"modello_testa": "TP60", "min_mandrini": "3", "min_maschere": -1},
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body["modello_testa"], "TP60")
        self.assertEqual(body["min_mandrini"], 3)
        # Invalid or negative values fall back to the default of 1.
        self.assertEqual(body["min_maschere"], 1)
        self.assertEqual(body["min_tastatori"], 1)
        pk = body["id"]
        self.assertEqual(
            self.last_log("anagrafica_crea").descrizione,
            "Creata configurazione testa TP60",
        )

        resp = self.put(
            f"/api/config-testa/{pk}/",
            {"modello_testa": "RENAMED", "min_generatori": 2, "min_mandrini": "abc"},
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        cfg = ConfigurazioneTesta.objects.get(pk=pk)
        self.assertEqual(cfg.modello_testa, "TP60")
        self.assertEqual(cfg.min_generatori, 2)
        self.assertEqual(cfg.min_mandrini, 3)
        self.assertEqual(
            self.last_log("anagrafica_modifica").descrizione,
            "Aggiornata configurazione testa TP60",
        )

        resp = self.post("/api/config-testa/", {"modello_testa": "tp60"})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(
            resp.json()["error"], "Configurazione per 'tp60' già esistente."
        )
        resp = self.post("/api/config-testa/", {})
        self.assertEqual(
            resp.json()["error"], "Il campo 'modello_testa' è obbligatorio."
        )
        resp = self.client.delete("/api/config-testa/9999/")
        self.assertEqual(resp.json()["error"], "Configurazione non trovata.")

        resp = self.client.delete(f"/api/config-testa/{pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            self.last_log("anagrafica_elimina").descrizione,
            "Eliminata configurazione testa TP60",
        )

    def test_read_only_accounts_are_refused(self) -> None:
        from ui.tests.helpers import create_reader

        self.client.force_login(create_reader("lettore-anagrafiche"))
        resp = self.post("/api/stabilimenti/", {"nome": "X"})
        self.assertEqual(resp.status_code, 403)
