# Changelog

## 2026-10-08

### Changed

- **Nuovo strato di lettura `ui/services/planning_read.py`.** Pianificazione,
  Gantt, kanban e bridge tablet leggono le attivita' dallo stesso modulo, che
  non scrive mai sul database e costa 5 query qualunque sia il numero di
  attivita' (prima: due query per attivita' solo per i minimi di risorse).
  Le funzioni `_fetch_tasks`, `_fetch_grouped`, `_fetch_jobs`,
  `_attivita_to_task_dict`, `_compute_activity_status` e
  `_aggregate_job_status` lasciano `ui/views.py` e diventano pubbliche in
  `planning_read` (`fetch_tasks`, `fetch_grouped`, `fetch_jobs`,
  `attivita_to_task_dict`, `compute_activity_status`, `aggregate_job_status`).
- **Lo stato lo calcola solo il server.** Ogni riga porta `status_pills`,
  `state_key` e `delay_days`; ogni commessa porta `status` / `status_label`.
  Le tre copie JavaScript della regola (`_computeActivityStatus` e
  `_pianDelayDays` in Pianificazione, `taskIsLate` / `taskDelayDays` /
  `summarizeCommessaTasks` nel Gantt) non ricalcolano piu' nulla: leggono i
  campi del server. Le risposte di `/api/tasks/update/`, `/api/jobs/update/`
  e `/api/attivita/salva/` restituiscono la riga aggiornata (`task`) e la
  commessa (`job`); `/api/gantt/` aggiunge `jobs` (stato per `job_id`).
- La riconciliazione dei problemi (`detect_for_all_activities`) non e' piu'
  dentro la lettura: la vista Pianificazione e `/api/gantt/` la chiamano
  esplicitamente prima di leggere. Resta una scrittura su GET, da rimuovere
  nel passo successivo.
- `get_testa_min_requirements`, `resource_shortfalls` e
  `missing_minimum_resources` accettano `configs=` (da `load_testa_configs()`)
  per risolvere i minimi di molte attivita' senza una query ciascuna.
- **Le letture non scrivono piu'.** Pianificazione e `/api/gantt/` non
  eseguono piu' la riconciliazione dei problemi (`detect_for_all_activities`
  + `auto_resolve_resource_overlaps`, 257 query con 19 attivita'): la fanno
  i percorsi di scrittura, dentro `ui/services/reconcile.py`. Il wizard, la
  cancellazione di attivita' e commesse e l'applicazione di una proposta di
  ottimizzazione ora riconciliano subito (prima lo faceva il caricamento
  pagina successivo). Le pagine sincronizzano solo le note «ritardo», che
  dipendono dal calendario, una volta al giorno per processo.
- Nuovo comando `python manage.py reconcile_problems` da schedulare ogni
  notte: riallinea tutte le note (acquisto, spostamento, sovrapposizione,
  ritardo) senza aspettare una scrittura.
- La rilevazione «spostamento» cerca gli stabilimenti delle risorse con una
  query per categoria invece di una per risorsa.

### Fixed

- **Un'attivita' in corso tiene le risorse fino a oggi.** La finestra di
  occupazione di un'attivita' avviata e non chiusa e' ora
  `[actual_start, max(data_fine, fine stimata, oggi)]`: prima, superata la
  fine stimata, le risorse risultavano libere e i conflitti non venivano visti.
- **La fine stimata conta i giorni lavorativi.** `effective_finish` distende
  la durata pianificata (lun–ven, come il solver) a partire da
  `actual_start`, non piu' i giorni di calendario. Il Gantt usa la stima del
  server (`effective_finish` su ogni riga) invece di ricalcolarla.
- **Lo stato non e' «in ritardo» il giorno dell'inizio previsto**: lo diventa
  dal giorno dopo, e il contatore dei giorni di ritardo parte da li'.
- **Il rischio penale guarda anche il piano**: una commessa con un'attivita'
  pianificata oltre la consegna, anche se non iniziata, e' «rischio penale».
- **Nessuna sostituzione automatica su un'attivita' avviata**: le risorse di
  un'attivita' in corso non vengono piu' scambiate dall'auto-risoluzione delle
  sovrapposizioni; resta la nota aperta. Le liste di disponibilita' per una
  sostituzione (manutenzione, scansione) usano la finestra reale, non quella
  pianificata.
- `/api/tasks/update/` e il wizard rifiutano con 400 una fine prevista
  precedente all'inizio. In `update_attivita` il controllo conflitti gira una
  volta sola e la rilevazione problemi e' nella stessa transazione del
  salvataggio.

- **Commessa e predecessori sono relazioni** (migrazioni `0034`–`0037`).
  `attivita.job_id` e' una chiave esterna verso `job` (stessa colonna, stessi
  valori): salvare un'attivita' per una commessa senza riga `job` la crea, e
  una commessa non si puo' rinominare. I predecessori vivono nella tabella
  `attivita_predecessori`: rinominare un'attivita' non rompe piu' i legami che
  la citano. La migrazione crea i `job` mancanti, collega ogni attivita' e
  converte il testo dei predecessori; i riferimenti che non risolvono vengono
  stampati e scartati. I payload restano uguali (`commessa` e `predecessors`
  come stringhe). Nuovo modulo `ui/services/precedences.py`.
- Prima di migrare un database esistente fare un backup (`pg_dump`, vedi
  README): la migrazione `0035` ricrea la colonna `job_id`.

- **Motore di ottimizzazione (`src/optimization`).**
  - Il limite di tempo (`SOLVER_TIME_LIMIT`) vale per l'intera risoluzione:
    prima valeva per ciascuna delle cinque fasi, quindi fino a cinque volte.
    La fase principale ha la quota maggiore e, se non trova una soluzione,
    riceve tutto il tempo residuo.
  - Nessuna commessa viene ignorata: una commessa senza penale pesa un
    centesimo a settimana di ritardo e una nuova fase minimizza i giorni di
    ritardo totali, cosi' una commessa in ritardo comunque finisce il prima
    possibile invece di scivolare a fine orizzonte.
  - La compatibilita' testa/generatore viaggia dentro il problema
    (`SchedulingProblem.generator_compatibility`) invece di una variabile
    globale modificata a ogni richiesta. Le regole sono allineate a quelle del
    picker: modello confrontato senza maiuscole, passo con tolleranza, un
    modello senza generatori configurati accetta qualunque generatore.
  - Le quantita' richieste dal solver sono i minimi di pianificazione
    (`get_testa_min_requirements`, TP60 Custom compreso); prima un modello di
    testa non configurato non generava alcun requisito di testa/generatore.
  - La penale settimanale e' sempre `costo × penale% / 100`: senza costo era
    usata la percentuale come importo.
  - La finestra bloccata di un'attivita' avviata e' `occupation_window`
    (giorni lavorativi, fino a oggi), non piu' una seconda definizione.
  - Lo stabilimento non e' piu' validato contro una lista fissa nel codice.

- **Bridge tablet.** Gli endpoint di scrittura eseguono direttamente le view
  web sulla richiesta del tablet (stesso metodo, stesso body, stessa sessione)
  invece di ricostruire una richiesta finta con `RequestFactory` e
  ricodificare il JSON. Aggiornamento e cancellazione attivita' condividono
  con il web le funzioni `tasks_update_response` / `attivita_delete_response`:
  il tablet riceve ora anche la riga aggiornata (`task`) e la commessa (`job`).
  Risposte, regole e log (`source: tablet`) sono identici sui due canali.

- **Una sola definizione delle categorie di risorse e del parsing.**
  `ui/services/resource_types.py` definisce i cinque tipi nelle tre
  grafie (`tipo`, `categoria` del wizard, tabella Inventario), le etichette,
  il modello ORM di ciascun tipo e le conversioni tra dizionario del wizard e
  coppie `(tipo, uid)`; `RisorsaAttivita.TIPO_CHOICES` ne deriva. Prima la
  stessa mappa era scritta sei volte, le etichette tre. `ui/services/parsing.py`
  raccoglie la lettura del body JSON (sostituisce 18 blocchi `try/except`
  nelle view), le date (quattro parser locali in meno) e gli importi delle
  commesse; le misure restano in `measures.py`. Nessun cambio di comportamento,
  tranne `/api/ottimizzazione/solve/` che con un body non valido risponde 400
  invece di trattarlo come vuoto.

- **Un solo handler per le anagrafiche.** Stabilimenti, responsabili, tipi
  attivita' e configurazioni testa condividono le view di creazione e di
  modifica/cancellazione in `ui/views_anagrafiche.py`, costruite da una
  descrizione della tabella (modello, campo nome, campi extra, parole
  italiane). Risposte, messaggi e voci di log sono invariati; `views.py`
  perde circa 500 righe.

- **Helper JavaScript condivisi e avanzamento calcolato dal server.**
  `ui/static/ui/common.js`, caricato da `base.html`, definisce una volta
  `esc`, `escOrDash`, `stabBadge`, `getCsrfToken` e `parseIsoDay`; le copie in
  Pianificazione, Inventario e nelle pagine di ottimizzazione sono sparite,
  `fmtDate` dei risultati usa quella del Gantt. I giorni lavorativi fatti e
  previsti di ogni attivita' (`work_done` / `work_total`) e la percentuale di
  avanzamento della commessa (`progress_pct`) arrivano dal server in ogni
  riga, risposta di scrittura e payload del Gantt: la regola non e' piu'
  ricalcolata in due copie JavaScript.

- **`optimization_explanation.py` sfoltito.** Il payload di sessione del
  wizard di ottimizzazione e' costruito in un solo posto
  (`current_plan_payload` + `build_result_payload_from_schedule`): la view
  `_run_optimization` ne aveva una copia di 90 righe. Delle quattro funzioni
  `format_*` ne restano due, il dizionario per l'LLM e la scorciatoia JSON
  per i notebook; `resolve_llm_payload` e i suoi messaggi guida sono spariti.
  La spiegazione viene formattata una volta sola invece di due.

### Removed

- Codice morto: `ui/forms.py` e i template del vecchio wizard a pagine
  (`attivita_aggiungi`, `attivita_pianificazione`, `attivita_risorse`), il
  template `ottimizzazione_parametri` (la route resta come redirect),
  `_get_draft` / `_draft_summary`, `problem_summary`, `_EXCEL_COLUMNS`,
  `JOB_STATUS_SORT_ORDER`, il wrapper `occupied_ids_with_delay`, l'alias
  JavaScript `filterActiveTable` in Pianificazione, le chiamate a
  `ensure_django` dentro i servizi (resta come helper per i notebook),
  `src/optimization/visualization.py` e la dipendenza `matplotlib`.
- `restore_resolved_notes`: saltava tutti i tipi di problema, quindi non
  ripristinava mai nulla.
- `src/optimization/db_problem.py`, `mockup_db.py` e il formato di id
  attivita' «da snapshot SQLite»: l'app legge solo da PostgreSQL.
- La tabella `risoluzione_problema` (mai scritta ne' letta) e la colonna
  testo `attivita.predecessors`, sostituita dalla relazione.
- Il riconoscimento «legacy» dei `job_id` con suffisso item nella selezione
  delle commesse per l'ottimizzazione: decide la chiave esterna.


## 2026-09-29

### Changed

- **PostgreSQL e' il database di riferimento.** Attivita', commesse,
  assegnazioni, inventario e configurazioni vivono in PostgreSQL, configurato
  dalle chiavi `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` e
  `DB_CONN_MAX_AGE` del file `.env`. I dati restano dopo logout e riavvii e gli
  id delle attivita' sono stabili. Il server puo' girare anche con piu' worker.
- **L'app non dipende piu' da MS Project ed Excel.** `dataset.mpp` e `jobs.xlsx`
  non vengono piu' importati al primo login ne' riscritti all'ultimo logout.
  I dati sono stati migrati una sola volta (vedi `docs/dataset.md`).

### Removed

- Import e write-back di `.mpp`/`.xlsx`, con la coda `MppSyncQueue`, lo stato
  `SyncSessionState` e i campi `synced_to_mpp` / `imported_from_mpp`
  (migrazione `0033`).
- Gli endpoint `/api/status/` e `/api/heartbeat/`, il banner di stato e
  l'overlay «Scrittura file in corso».
- Il comando `flush_to_sources`.
- Le chiavi `.env` `SERVER_DIR`, `MPP_PATH`, `XLSX_PATH`, `HEARTBEAT_*`,
  `MSP_DISPATCH_RETRY_*` e `AUTO_IMPORT_ON_STARTUP`.
- Le dipendenze `pywin32` e `tqdm`.

### Compatibilita' tablet

- `/api/bridge/status/` risponde sempre `status: "ready"`, con gli stessi campi
  di prima.
- `/api/bridge/import/` risponde **410**.
- `/api/bridge/app-settings/` non restituisce piu' percorsi file, heartbeat e
  parametri MS Project.

### Unchanged

- «Importa da Excel» in Inventario resta: carica le risorse direttamente nel
  database.

## 2026-09-25

### Changed

- **TP60 Custom richiede 2 mandrini e 1 maschera.** Con il flag *TP60 Custom
  (Sporgenza Tubo-Piastra)* i minimi degli accessori non sono piu' tutti a zero:
  servono sempre 2 mandrini e 1 maschera, qualunque cosa dica la configurazione
  della testa, e solo il tastatore non e' richiesto. Sono i minimi usati sia per
  la preselezione nel wizard sia per i problemi di `acquisto`
  (`ui.services.activity_inventory.TP60_CUSTOM_REQUIREMENTS`).

## 2026-09-10

### Added

- **L'inizio effettivo non puo' essere una data futura.** Registrare l'inizio di
  un'attivita' registra un fatto, e un fatto non puo' avere la data di un giorno
  che non e' ancora arrivato: l'operatore puo' indicare solo la data odierna o
  una data passata. La regola vive in
  `ui.services.bridge_write.validate_actual_start_not_future` e vale per
  entrambe le vie di scrittura — `/api/tasks/update/` e il ponte tablet — che
  rispondono **400** con un messaggio che nomina sia la data digitata sia
  quella odierna. In Pianificazione il selettore di «Inizio effett.» si ferma a
  oggi (`max`) e il pannello ripete il controllo prima di partire, cosi' il
  rifiuto e' immediato. Viene controllato solo un valore *nuovo*: un'attivita'
  che porta gia' un inizio effettivo futuro, arrivato dal .mpp prima che la
  regola esistesse, resta modificabile e puo' essere corretta. Le date
  pianificate (`start` / `finish`) non sono toccate: programmare in avanti e'
  esattamente il loro scopo.

### Fixed

- **La disponibilita' e' rossa anche per teste e generatori in manutenzione.**
  In Inventario il pallino diventava rosso solo quando un'attivita' in corso
  occupava la risorsa: una testa o un generatore in manutenzione restava verde
  pur non essendo utilizzabile. Ora `in_manutenzione` prevale sulle
  occupazioni: il pallino e' rosso, l'etichetta e' «In manutenzione» e il
  tooltip lo dice, elencando le attivita' eventualmente ancora pianificate. Il
  descrittore porta la nuova chiave `in_manutenzione`, che arriva anche al
  tablet attraverso il ponte. Accendere o spegnere l'interruttore, o salvare la
  scheda della risorsa, restituisce la disponibilita' ricalcolata, cosi' il
  pallino si aggiorna senza ricaricare la pagina.

- **Accettando una proposta le risorse ora arrivano anche nel file .mpp.**
  `apply_optimization_result` riscriveva le assegnazioni sul database ma
  accodava per il write-back un payload senza la chiave `resources`, e
  `_apply_update_task` tocca le risorse del task solo quando quella chiave
  c'e': nel .mpp restavano quelle vecchie mentre a video ne comparivano di
  nuove. Riguardava sia «Accetta» sulla proposta di ripianificazione della
  singola attivita' sia l'accettazione del risultato dell'ottimizzazione, dove
  la riassegnazione delle risorse e' il senso stesso dell'operazione. La chiave
  viene ora inviata sempre, lista vuota compresa, cosi' che una proposta che
  lascia l'attivita' senza risorse le azzeri anche nel .mpp.

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
