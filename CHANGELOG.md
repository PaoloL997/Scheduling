# Changelog

## 2026-09-07

### Added

- **Lo sforamento della durata stimata si vede nel Gantt.** Un'attivita'
  iniziata e non ancora chiusa la cui fine stimata (inizio effettivo +
  durata da preventivo) e' gia' passata prosegue con una linea orizzontale
  tratteggiata fino a oggi. Finche' sfora, l'unica barretta verticale di
  fine e' quella su oggi: sulla fine stimata comincia il tratteggio, non
  c'e' una seconda fine. I badge di stato si spostano dopo lo sforamento.
  Nuova voce in leggenda: «Sforamento della durata stimata (fino a oggi)».

### Fixed

- **Un'attivita' ancora aperta oltre la fine da preventivo e' «IN RITARDO».**
  Prima il ritardo scattava solo per chi partiva dopo la data prevista o non
  era mai partito, cosi' un'attivita' iniziata puntuale e mai chiusa restava
  solo «IN LAVORAZIONE». Ora porta entrambe le etichette e rientra nel
  contatore «Attivita' in ritardo». Regola allineata in `_compute_activity_status`
  (`ui/views.py`), nei badge e nelle statistiche del Gantt (`gantt.js`) e nelle
  pillole di Pianificazione.
- **La proposta di ripianificazione e' calcolata quando apri il problema.**
  Prima veniva risolta in anticipo al caricamento della pagina e salvata: se
  nel frattempo cambiavi un'altra attivita' restava li', vecchia e non piu'
  valida. Ora il modal chiama `GET /api/attivita/<pk>/riprogramma/proposta/`
  a ogni apertura e mostra il risultato appena pronto; il payload salvato
  serve solo a «Accetta», che applica esattamente cio' che hai visto. Una
  proposta rifiutata resta rifiutata e non viene ricalcolata; gli account in
  sola lettura la vedono senza che venga scritta. Il caricamento delle pagine
  non fa piu' girare il solver.
- **La proposta di ripianificazione compare anche senza sovrapposizione.**
  Il backend la associa a ogni riga aperta ripianificabile (`sovrapposizione`
  e `ritardo`), ma il modal la mostrava solo sulle righe `sovrapposizione`:
  un'attivita' in ritardo che non condivide risorse con nessuno (25078-02-SNA
  e SNB) aveva la proposta calcolata e invisibile. Ora viene mostrata una sola
  volta sulla prima riga aperta che la porta, qualunque sia il tipo.
- **Nel modal problemi/note le voci aperte stanno in cima**, quelle risolte
  sotto; dentro ogni gruppo resta l'ordine di creazione.
- Il gantt della proposta di ripianificazione elenca le attivita' nello
  stesso ordine del gantt principale: la chiave di ordinamento e' ora
  `(job_id, data_inizio, activity_id)`, la stessa di `_fetch_tasks`, al
  posto di `(inizio, etichetta)`. Le due coincidevano finche' tutte le
  attivita' avevano una data di inizio pianificata.
- La nota di tipo `ritardo` mostrava la chiave grezza «ritardo» al posto
  dell'etichetta: ora e' «Attivita' in ritardo».
- **I riferimenti fra attivita' si confrontano senza distinzione di
  maiuscole.** Rinominare un'attivita' ne ricostruisce l'`activity_id` ma
  non riscrive i `predecessors` che la puntano: 25078-02-SNA/SNB/SNC
  riferivano `25078-02-qualifica` mentre l'id era `25078-02-Qualifica`.
  Ne conseguiva la freccia di dipendenza mancante nel Gantt, il nome del
  predecessore non risolto nel tooltip e - piu' grave - il vincolo di
  precedenza scartato dal solver, che proponeva date senza attendere la
  fine della Qualifica. Nuovo helper `normalize_activity_id` in
  `ui/services/identifiers.py`, usato da `gantt.js`, dal builder del
  problema e dalla risoluzione dei nomi.

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
