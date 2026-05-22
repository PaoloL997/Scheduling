# Dataset — come funziona

Questo documento descrive l'intero ciclo di vita del dataset nell'applicazione: dalle sorgenti dati originali fino alla visualizzazione, compresa la gestione del lock sul file MS Project e il write-back delle modifiche.

---

## Sorgenti dati

| File | Scopo |
|------|-------|
| `dataset.mpp` | File MS Project — sorgente principale di task, risorse e assegnazioni |
| `jobs.xlsx` | File Excel — dati complementari (commesse, job) |

Entrambi i file vengono usati anche **al di fuori dell'applicazione**. Il `.mpp` in particolare può essere aperto in scrittura da una sola applicazione alla volta.

---

## Database Django (`db.sqlite3`)

L'app usa un unico database SQLite gestito da Django ORM:

- Contiene utenti, sessioni, attività, risorse inventario, coda di write-back (`MppSyncQueue`)
- Persiste tra i riavvii del server
- Le tabelle `Attivita`, `RisorsaAttivita` e le tabelle inventario vengono **svuotate e ricaricate** ad ogni sessione (al login del primo utente)

---

## Ciclo di vita del dataset

### 1. Avvio del server

All'avvio (`AppConfig.ready()`) viene eseguito soltanto `configure()`, che salva i path dei file sorgente. **Nessun import viene avviato** — il `.mpp` non viene toccato finché nessun utente accede.

### 2. Primo login

Al login del primo utente il segnale `user_logged_in` esegue in sequenza:

1. **Controllo staleness** — `is_mpp_stale()` verifica se il `.mpp` è stato modificato dall'ultimo import (oppure se non è mai stato importato).
2. **Reset dataset app** (se stale) — vengono cancellati tutti i record `Attivita`, `RisorsaAttivita` (cascade) e `MppSyncQueue`. Questo garantisce che le modifiche apportate esternamente al `.mpp` siano la fonte di verità.
3. **Re-import** (se stale) — `trigger_reimport()` avvia in background un thread che:
   - Registra il `mtime` del `.mpp` **prima** di aprirlo
   - Legge `.mpp` in sola lettura (nessuna modifica al file)
   - Legge `.xlsx`
   - Scrive direttamente nel Django ORM (`Attivita`, `RisorsaAttivita`, inventario)
   - Imposta lo stato su `READY`
4. **Acquisizione lock COM** — `project_session.register_session(session_key)` avvia il thread di lock:
   - Aspetta che l'import raggiunga lo stato `READY`
   - Apre il `.mpp` via COM (`win32com`) in modalità scrittura → **il file viene bloccato a livello OS**
   - Verifica che il file non sia già in sola lettura (aperto altrove)
   - Si mette in attesa

```
Login
  └─► is_mpp_stale?
        ├─ Sì → reset Attivita + MppSyncQueue → trigger_reimport()
        └─ No → (database.db già aggiornato)
  └─► register_session()
        └─► _hold_thread: aspetta READY → FileOpen(.mpp) via COM → in attesa...
```

### 3. Utenti attivi

Con almeno un utente loggato:

- Il `.mpp` è **bloccato** — nessuna applicazione esterna può aprirlo in scrittura
- Le modifiche create via web (nuove attività, risorse) vengono scritte nel `db.sqlite3` e accodate in `MppSyncQueue`
- La pagina di pianificazione mostra sia i task importati dal Django ORM sia le attività non ancora sincronizzate (`Attivita.synced_to_mpp=False`) — queste ultime sono iniettate in tempo reale nel rendering senza attendere il write-back

### 4. Logout dell'ultimo utente

Quando l'ultimo utente fa logout, `unregister_session()` sveglia il thread di lock che esegue il write-back:

1. **`write_pending_changes()`** — processa ogni entry in `MppSyncQueue` in ordine di creazione:
   - `add_task` → aggiunge il task al `.mpp` via COM, imposta `Attivita.synced_to_mpp=True`
   - `add_resource` → aggiunge la risorsa al `.mpp`
   - `delete_resource` → elimina la risorsa dal `.mpp`
2. **`app.FileSave()`** — salva il `.mpp` solo se il write-back è andato a buon fine integralmente; se un'operazione fallisce il file **non viene salvato**
3. **`app.FileCloseEx()` + `app.Quit()`** — rilascia il lock COM

```
Ultimo logout
  └─► _hold_thread svegliato
        └─► write_pending_changes()
              ├─ add_task        → .mpp + synced_to_mpp=True
              ├─ add_resource    → .mpp
              └─ delete_resource → .mpp
        └─► FileSave() (solo se tutto ok)
        └─► FileCloseEx() → lock rilasciato
```

### 5. Riavvio del server con sessioni attive

Se il server viene riavviato mentre utenti hanno sessioni attive (cookie ancora validi), il middleware `SessionTrackingMiddleware` intercetta la prima richiesta autenticata e chiama `register_session()` — il lock viene riacquisito senza richiedere un nuovo login.

---

## Stato del dataset nel frontend

Il banner in basso a sinistra mostra lo stato corrente tramite polling sull'endpoint `/api/status/`:

| Stato | Significato |
|-------|-------------|
| `not_started` | Nessun utente ancora loggato — import non avviato |
| `loading` | Import in corso |
| `ready` | Dataset disponibile |
| `error` | Import fallito (clicca sul banner per i dettagli) |

---

## File coinvolti

| File | Ruolo |
|------|-------|
| `ui/apps.py` | `ready()` → `configure()` al boot |
| `ui/signals.py` | Gestisce login/logout; decide se re-importare e resettare i dati |
| `ui/services/importer.py` | Thread di import, stato, staleness check |
| `ui/services/project_session.py` | Singleton che gestisce il lock COM e il thread di write-back |
| `ui/services/writeback.py` | Applica le `MppSyncQueue` entries al `.mpp` aperto |
| `ui/middleware.py` | `SessionTrackingMiddleware` — recupera sessioni sopravvissute al restart |
| `src/data/import.py` | Import effettivo dal `.mpp` e `.xlsx` → Django ORM |
| `src/data/project.py` | Wrapper COM attorno a `MSProject.Application` |
| `ui/models.py` | `Attivita`, `RisorsaAttivita`, `MppSyncQueue` |

---

## Note operative

- **Processo singolo** — il lock COM è in-memory: non usare deployment multi-processo (gunicorn con workers > 1). Usare un singolo worker o un thread server (es. `waitress`, `runserver`).
- **Logout esplicito** — le sessioni che scadono senza logout non rilasciano il lock finché il server non viene riavviato. Il file resta bloccato.
- **Write-back atomico** — se anche una sola operazione in `MppSyncQueue` fallisce, il `.mpp` non viene salvato. Le entry rimangono in coda e verranno ritentate alla prossima sessione.
