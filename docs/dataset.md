# Dataset — come funziona

Questo documento descrive dove vivono i dati dell'applicazione e come vengono letti e modificati.

---

## Database di riferimento

Tutti i dati risiedono in un database **PostgreSQL**, configurato dal file `.env`:

| Chiave | Default | Significato |
|--------|---------|-------------|
| `DB_NAME`, `DB_USER`, `DB_PASSWORD` | — (obbligatorie) | Database, ruolo e password |
| `DB_HOST`, `DB_PORT` | `localhost`, `5432` | Indirizzo del server |
| `DB_CONN_MAX_AGE` | `60` | Secondi di riuso di una connessione (0 = una per richiesta) |

Se manca una chiave obbligatoria il server non parte e il messaggio indica quale.

Il database contiene:

- utenti, profili e sessioni;
- attività (`attivita`) e risorse assegnate (`risorse_attivita`);
- commesse (`job`);
- inventario (`testa_item`, `mandrino_item`, `tastatore_item`, `maschera_item`, `generatore_item`);
- configurazioni (`stabilimento`, `responsabile`, `tipo_attivita`, `configurazione_testa`, `generatore_compatibile`);
- problemi operativi (`problema_attivita`, `risoluzione_problema`) e registro operazioni (`operation_log`).

I dati **persistono**: logout, scadenza delle sessioni e riavvii del server non li toccano.

---

## Lettura e scrittura

- **Web** — le viste in `ui/` leggono e scrivono tramite l'ORM di Django.
- **Tablet** — il bridge JSON (`/api/bridge/...`) usa gli stessi servizi (`ui/services/bridge_write.py`, `ui/services/inventory.py`), quindi valgono le stesse regole di validazione.
- **Importa da Excel** (Inventario) — carica in blocco risorse da un file `.xlsx` direttamente nelle tabelle di inventario. È uno strumento di caricamento: il file non viene conservato né riletto.

Non ci sono né import all'avvio né write-back verso file esterni, e non esistono stati di "caricamento": il bridge risponde a `/api/bridge/status/` sempre con `status: "ready"`, e `/api/bridge/import/` risponde `410`.

Non c'è più stato in memoria legato ai dati, quindi l'app può girare anche con più worker.

---

## Migrazione da MS Project / Excel (29/09/2026)

Fino al 25/09/2026 le fonti di verità erano `dataset.mpp` (MS Project, via COM) e `jobs.xlsx`. Il database conteneva solo una copia di lavoro, importata al primo login e riscritta sui file all'ultimo logout.

Il passaggio a PostgreSQL è stato fatto una sola volta:

1. copia delle tabelle persistenti da `db.sqlite3` (utenti, profili, stabilimenti, responsabili, tipi attività, configurazioni teste, generatori compatibili, risoluzioni, registro operazioni, sessioni) con `dumpdata` / `loaddata`;
2. import di attività, assegnazioni, inventario e commesse da `dataset.mpp` e `jobs.xlsx` con il codice di import dell'epoca, seguito dal rilevamento dei problemi;
3. rimozione del codice di import/write-back e delle tabelle di sincronizzazione (`mpp_sync_queue`, `sync_session_state`, migrazione `0033`).

I file originali sono archiviati fuori dal repository e non vengono più letti.

---

## Backup

Il database è l'unica copia dei dati: va salvato con regolarità.

```bash
pg_dump -Fc -U scheduling -d scheduling -f scheduling-YYYYMMDD.dump
pg_restore -U scheduling -d scheduling --clean scheduling-YYYYMMDD.dump
```

---

## Note operative

- **Seed demo** — i comandi `seed_*` scrivono nel database configurato e i dati restano finché non si usa `--clear`: vanno eseguiti solo su un database di sviluppo.
- **Test** — `python manage.py test ui` crea `test_<DB_NAME>` sullo stesso server: il ruolo deve avere il permesso `CREATEDB`.
