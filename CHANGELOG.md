# Changelog

## 2026-09-04

### Fixed

- **Chiudere un'attivita' non puo' piu' essere rifiutato.** Registrare
  «Inizio/Fine effettiva» dichiara un fatto avvenuto: il salvataggio riesce
  sempre e l'eventuale conflitto diventa una nota sull'attivita' non ancora
  iniziata, con proposta di ripianificazione. La validazione bloccante resta
  solo quando si spostano davvero le date da preventivo o si cambiano le
  risorse. Il pannello di Pianificazione rimandava sempre `start`/`finish`
  anche invariati, per cui il controllo scattava a ogni salvataggio.
- **Una sola definizione di occupazione risorsa.** `occupation_window` in
  `resource_conflicts.py` e' l'unica regola; `find_resource_overlaps` e
  `overlap_reassign` la seguono invece di applicarne due varianti divergenti.
  Un'attivita' completata smette davvero di occupare oltre `actual_finish`;
  due attivita' mai iniziate che condividono una risorsa ora confliggono
  (prima serviva che almeno una fosse partita).
- **«Modifica risorse» non cancella piu' le assegnazioni.** La risorsa gia'
  assegnata compare sempre in elenco, preselezionata, con un badge che spiega
  perche' non sarebbe disponibile (occupata, in manutenzione, altro
  stabilimento, fuori requisiti). Prima veniva filtrata via in silenzio e
  confermare il picker la eliminava dal database.
- Il confronto sul modello testa e' `iexact` con `strip()`: differenze di
  maiuscole o spazi non escludono piu' la testa.
- `_RISORSE_TARGET` era una globale condivisa fra wizard e picker di modifica:
  ora e' per-flusso.
- Le date effettive sono validate: `actual_finish >= actual_start`, e una data
  malformata restituisce 400 invece di cancellare il campo in silenzio.
- `api_tasks_update`, `api_attivita_delete` e `api_jobs_delete` richiedono
  l'autenticazione e il token CSRF.
- Allineate le tre implementazioni dello stato attivita' (server, tabella
  Pianificazione, Gantt), incluso l'off-by-one su «in ritardo».

### Added

- Tipo di problema **`ritardo`**: un'attivita' mai iniziata e oltre la data di
  fine prevista genera una nota aperta e una proposta di ripianificazione. Si
  chiude da sola quando l'attivita' parte o viene ripianificata.
- Badge di stato **NON INIZIATA - SCADUTA** in Pianificazione e nel Gantt.
- `seed_overlap_demo --overdue` riproduce il caso segnalato (commessa 25078D).
- **Three access layers** stored on `UserProfile.ruolo`: `admin` (write access + Django admin panel), `scrittura` (view plus create/edit of commesse, attività, inventory and settings) and `lettura` (read-only).
- `ui.permissions` with the `write_required` / `admin_required` view decorators, applied to every mutating endpoint (web app and tablet bridge). Refusals return HTTP 403 — a JSON body for `/api/` calls.
- Admin panel: the role is editable inline on the user page and from a dedicated "Profilo e permessi" list; saving it syncs `is_staff` / `is_superuser`, so only admins can open `/admin/`.
- Templates hide the actions a read-only account cannot perform (`can_write` / `is_admin_user` context, `body.is-readonly` class, `window.CAN_WRITE`).

### Changed

- New accounts (registration form included) start as `lettura`; existing profiles were migrated to `scrittura`, superusers to `admin`.
- The tablet bridge `auth/login` and `auth/me` payloads carry `ruolo`, `can_write` and `is_admin`.


## 2026-08-26

### Added

- Automatic **reschedule proposal** when a resource overlap cannot be resolved by swapping: the existing CP-SAT model is run on the blocked activity and its precedence chain; other activities reserve their resources. Planning notes show dates, assigned resources, a mini-Gantt, and Accept / Reject.
- `seed_overlap_demo --reschedule` demo (R301 late / R305 blocked chain).
- `ProblemaAttivita.proposta_json` to persist the proposal without re-solving on every Gantt load.

### Changed

- Wizard optimization now reserves resources of **all** activities outside the selected set (including planned, not-started ones), not only started/completed work.
- Overlap problems are attached only to the activity that has not started.
- Home Gantt always shows the warning badge for open problems; click opens the notes modal.
