# Changelog

## 2026-09-10

### Changed

- **La lettura del WPS passa da Gemini a OpenAI.** L'estrazione di diametro, S1
  e passo tubi dal PDF usa ora la Responses API di OpenAI, con modello
  predefinito `gpt-5.6-sol` configurabile da `.env` con `OPENAI_WPS_MODEL`. Il
  PDF viene inviato in base64 e la risposta e' vincolata da uno schema
  strutturato: non serve piu' ripulire il JSON dal markdown ne' cercarlo con
  espressioni regolari. La chiamata ha ora un timeout di 90 secondi e un solo
  tentativo di rinvio — prima non aveva alcun timeout e il browser poteva
  restare in attesa a tempo indefinito. Chiave assente o non valida, credito
  esaurito, limite di richieste, timeout, rete e modello inesistente hanno
  ciascuno un messaggio in italiano che dice cosa controllare. Nuovo servizio
  `ui/services/wps_ai.py` al posto di `ui/services/gemini.py`.
- La card «Misure Gemini» del wizard si chiama ora **«Misure WPS»** e la
  risposta di `/api/attivita/analizza/` espone la chiave `misure` al posto di
  `gemini`.

### Removed

- Variabili `.env` `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_THINKING_LEVEL` e
  `GEMINI_THINKING_BUDGET`, sostituite da `OPENAI_API_KEY` e
  `OPENAI_WPS_MODEL`. Di conseguenza `/api/app-settings/` e
  `/api/bridge/app-settings/` espongono `openai_wps_model` al posto dei tre
  campi Gemini. Le due impostazioni «thinking» erano peraltro gia' inerti: il
  budget era fissato a zero nel codice e il livello non veniva mai letto.
- Dipendenza `google-genai`, sostituita da `openai` (finora presente solo come
  dipendenza indiretta di `langchain-openai`).
- Template del vecchio wizard WPS a piu' pagine (`attivita_wps.html` e
  `attivita_wps_conferma.html`): non erano raggiungibili da nessuna URL e
  contenevano gli ultimi riferimenti a Gemini.

## 2026-09-09

### Added

- **Colonna «Disponibilità» in Inventario.** Ogni tabella dell'inventario
  (generatori, mandrini, maschere, tastatori, teste) mostra un pallino verde se
  la risorsa oggi e' libera, rosso se e' occupata. Al passaggio del mouse (o da
  tastiera, con il focus sul pallino) si apre un riquadro con il dettaglio: sul
  rosso l'attivita' che la sta impegnando, il suo periodo e il giorno in cui si
  libera; sul verde fra quanti giorni e da quali attivita' verra' impegnata,
  oppure «Nessun impegno pianificato». Gli impegni consecutivi vengono
  concatenati, quindi la data di rilascio e' il primo giorno realmente libero.
- Nuovo servizio `ui/services/resource_availability.py`: l'occupazione non e'
  reimplementata, arriva da `resource_conflicts.occupation_window` tramite la
  nuova `resource_conflicts.bookings_not_ended`, cosi' un inizio effettivo in
  ritardo o una chiusura tardiva contano come per le sovrapposizioni.

## 2026-09-08

### Added

- **Un'attivita' non parte senza le risorse minime.** Registrare l'inizio
  effettivo di un'attivita' che non ha ancora il kit minimo — una testa, un
  generatore e, secondo la configurazione della testa, il numero minimo di
  ciascun accessorio — viene rifiutato con un 409 che elenca cosa manca
  («Testa 0/1; Generatore 0/1»). Vale per il pannello di Pianificazione e per
  il bridge tablet, che condividono `update_attivita`. La regola e' la stessa
  che genera le note `acquisto`: `resource_shortfalls` in
  `ui/services/problem_detection.py` e' l'unica definizione di «sotto il
  minimo», usata sia dalle note sia dal blocco dell'avvio. Solo il passaggio a
  «avviata» e' controllato, quindi le modifiche su un'attivita' gia' partita
  non vengono rifiutate. Il pannello mostra l'avviso «Avvio bloccato» sul campo
  «Inizio effett.» e blocca il salvataggio prima di chiamare il server: ogni
  riga porta i minimi in `data-min-requirements` e ogni attivita' espone
  `min_requirements` / `can_start` nel payload JSON.
- **Sostituzione di accessori compatibili via scansione dal tablet.** Nuovo
  endpoint `POST /api/bridge/attivita/<pk>/scan/` (`tipo`, `codice`,
  `atteso`, `conferma`). Teste e generatori restano a corrispondenza esatta di
  ID. Per mandrini, tastatori e maschere il codice scansionato puo' essere un
  pezzo diverso da quello pianificato purche' compatibile per misura (bore
  min–max, passo), con la stessa regola della pianificazione. Tre casi:
  pezzo libero → consentito con conferma esplicita («Stai usando X al posto di
  Y, va bene?») e pianificazione aggiornata; pezzo in uso su un'attivita' in
  lavorazione → vietato con motivo `occupata`; pezzo assegnato a un'attivita'
  pianificata sovrapposta → consentito con conferma, l'attivita' sottratta
  viene ricoperta automaticamente con il motore di riassegnazione esistente
  (`pick_free_spare`) e, se nessun pezzo compatibile e' libero, resta scoperta
  con il problema «Risorsa da recuperare» visibile in Pianificazione senza
  bloccare il tablet. Fuori tolleranza → vietato con misure esplicite. Nuovo
  tipo di nota `sostituzione` («Sostituzione da tablet») su entrambe le
  attivita' coinvolte.
- Servizio condiviso `ui/services/scan_substitution.py`; il selettore del
  sostituto usato dalla manutenzione e' promosso a
  `overlap_reassign.pick_free_spare` e riusato dalla scansione.

### Fixed

- **Un aggiornamento risorse parziale non cancella piu' le altre categorie.**
  `update_attivita` sostituiva tutte le assegnazioni con quelle passate: una
  richiesta con la sola categoria toccata azzerava silenziosamente teste,
  tastatori, maschere e generatori. Ora una categoria assente in `risorse`
  conserva le assegnazioni correnti; solo una lista vuota esplicita la svuota.
  Coperto da test.
- **Le misure sono confrontate in modo indipendente dalla cultura.** Diametri e
  passi sono testo in inventario: il `CAST` SQL leggeva «12,5» come 12 e un
  valore non numerico come 0, falsando il filtro di compatibilita'. Nuovo
  modulo `ui/services/measures.py` (`parse_measure`, `is_resource_compatible`)
  usato dal selettore risorse, dalla preselezione, dall'ottimizzatore e dalla
  scansione: virgola e punto sono equivalenti, i valori non convertibili sono
  esclusi dal confronto e mai trattati come zero.

## 2026-09-07

### Added

- **TP60 Custom (Sporgenza Tubo-Piastra)** flag in the add-activity wizard, under the *Misure Gemini* card. Only testa and generatore stay required: mandrini, tastatori and maschere are not pre-selected and, left unselected, no longer raise an `acquisto` problem. Their lists stay populated so one can still be ticked by hand. Stored on `Attivita.tp60_custom`.
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
