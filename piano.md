# Piano finale — Sistema di scheduling ottimo delle attività

Documento di specifica e piano di sviluppo (no-code). Consolida tutte le decisioni prese.
Sostituisce la bozza `analisi-scheduling.md`.

---

## 1. Obiettivo in una frase

Schedulare le attività di tutti i job assegnando le risorse necessarie, in modo da **minimizzare la
penale totale per ritardo di consegna** (idealmente azzerarla).

---

## 2. Decisioni e assunzioni consolidate

Queste sono le regole confermate con cui costruire il sistema:

1. **Durata attività** = numero di **giorni lavorativi** necessari a completarla.
2. **Attività di un job in catena lineare**: un'attività può iniziare solo quando la precedente
   (dello stesso job) è conclusa.
3. **Penale** = importo per **settimana di ritardo**, **arrotondata per eccesso** (1 giorno di
   ritardo = 1 settimana).
4. **Calendario lavorativo lun–ven** (sabato e domenica non lavorativi). **Festività ignorate** per ora.
5. **Nessun costo** di trasferimento né di acquisto, e **nessun tempo** logistico:
   trasferimenti e acquisti si considerano **immediati e gratuiti** (vedi §8 e l'implicazione importante).
6. **Earliest start opzionale**: alcune attività hanno una **data minima di schedulazione** imposta
   dal cliente; se non specificata, l'attività può essere collocata ovunque nell'orizzonte.
7. **Matching maschera** = misura (`tube_pitch`) **esatta**.
8. **Requisiti generati dai dati**: un'attività richiede una risorsa **solo se il relativo attributo
   è specificato** (vedi §5). Le **quantità** richieste sono **definite in input**.
9. **Consegna del job** = fine dell'**ultima** attività della catena.

---

## 3. Entità del dominio

| Entità | Descrizione |
|---|---|
| **Job** | Commessa = sequenza ordinata di 1+ attività. |
| **Activity (Attività)** | Singola lavorazione di un job, eseguita in uno stabilimento. |
| **Plant (Stabilimento)** | Sede produttiva; possiede una dotazione iniziale di risorse. |
| **Resource (Risorsa)** | Mezzo a capacità unitaria (1 attività per volta). 5 tipi: mandrino, tastatore, maschera, testa, generatore. |

---

## 4. Dati di input (schema)

**Job**
- `job_id`
- `due_date` (data di consegna)
- `penalty_per_week` (penale per settimana di ritardo)
- elenco **ordinato** delle attività

**Activity**
- `activity_id`, `job_id`, `ordine` (posizione nella catena)
- `plant` (stabilimento)
- `durata_giorni` (giorni lavorativi)
- `earliest_start` (opzionale; se assente = inizio orizzonte)
- attributi di requisito (presenti solo se applicabili — vedi §5):
  - `bore_min`, `bore_mid`, `bore_max`
  - `tube_pitch`
  - `modello_testa`
- `quantita` per ciascun tipo di risorsa richiesto (definite in input)

**Plant**
- `plant_id`
- dotazione iniziale di risorse (lista delle risorse fisiche presenti)

**Resource** (inventario, ogni unità identificata singolarmente)
- `resource_id`, `tipo` ∈ {mandrino, tastatore, maschera, testa, generatore}
- `home_plant`
- attributi: mandrino/tastatore → `bore`; maschera → `tube_pitch`; testa/generatore → `modello_testa`

**Globale**
- `data_inizio_orizzonte` e lunghezza dell'`orizzonte` di pianificazione

---

## 5. Quali risorse richiede un'attività (generazione dei requisiti)

La presenza di un requisito dipende dagli **attributi specificati** sull'attività:

| Attributo specificato | Risorse richieste | Regola di compatibilità |
|---|---|---|
| `bore_min/mid/max` | **mandrino** e **tastatore** | risorsa ammissibile se `bore ∈ [bore_min, bore_max]`; **preferita** se `bore = bore_mid` |
| `tube_pitch` | **maschera** | `tube_pitch` **esatto** |
| `modello_testa` | **testa** e **generatore** | `modello_testa` coincidente; testa e generatore devono riferirsi allo **stesso** modello |

- Se un attributo **non** è specificato, l'attività **non** richiede quel tipo di risorsa.
- Le **quantità** per ogni tipo richiesto sono prese dall'input (di norma 1, ma configurabili).
- `bore_mid` è una **preferenza soft**: se non c'è una risorsa con `bore = mid`, se ne usa una
  ammissibile (in `[min, max]`), pagando solo una penalità di spareggio (mai un aumento della penale di ritardo).

---

## 6. Vincoli del modello

1. **Precedenza in catena (intra-job)**
   Ogni attività inizia solo dopo la fine della precedente dello stesso job:
   `fine(attività k) ≤ inizio(attività k+1)`.

2. **Earliest start (opzionale)**
   `inizio(attività) ≥ earliest_start` quando specificato; altrimenti nessun limite inferiore oltre l'orizzonte.

3. **Copertura dei requisiti**
   Ogni attività riceve esattamente la **quantità richiesta** di ogni tipo di risorsa, scelta tra le
   risorse **compatibili** (§5).

4. **Capacità unitaria della risorsa**
   Ogni risorsa fisica è assegnata a **una sola attività per volta**: gli intervalli temporali delle
   attività che usano la stessa risorsa **non si sovrappongono** (vincolo di non sovrapposizione).

5. **Coerenza testa–generatore**
   Quando è richiesto `modello_testa`, la testa e il generatore assegnati devono appartenere allo
   **stesso** modello.

6. **Calendario lavorativo**
   La durata avanza solo nei giorni lavorativi (lun–ven). Il tempo si modella su indice di **giorni
   lavorativi**; sabato/domenica non avanzano la lavorazione.

> **Non** ci sono vincoli di tempo/costo per trasferimenti o acquisti (vedi §8).

---

## 7. Obiettivo

**Primario (unica metrica economica):** minimizzare la **penale totale**

```
minimizza  Σ_job  penalty_per_week(job) × settimane_di_ritardo(job)
```

dove `settimane_di_ritardo(job) = ⌈ giorni_di_ritardo(job) / 7 ⌉` (arrotondamento per eccesso),
e `giorni_di_ritardo = max(0, data_fine_ultima_attività − due_date)`.

**Secondario (solo spareggio, nessun costo reale):** a parità di penale, scegliere le soluzioni che
rispettano le preferenze, con pesi unitari piccoli che **non possono mai** aumentare la penale primaria:
- preferire `bore = bore_mid`;
- preferire **trasferimento** per teste/generatori e **acquisto** per mandrini/tastatori/maschere
  (rilevante solo se si decide di tracciare queste scelte — vedi §8).

Approccio raccomandato: **lessicografico** (prima minimizzare la penale, poi i criteri di spareggio).

---

## 8. Trasferimenti, acquisti e ruolo degli stabilimenti

Per scelta progettuale, **trasferimenti e acquisti sono immediati e senza costo** (nessun tempo,
nessun importo). Conseguenze:

- La **posizione** di una risorsa (stabilimento) **non vincola** la sua disponibilità: una risorsa
  può servire un'attività in qualunque stabilimento senza ritardo. Lo stabilimento resta un dato
  descrittivo/di reporting, non un vincolo temporale.
- Il vero vincolo che resta sulle risorse è la **capacità unitaria** (1 attività per volta) e la
  **disponibilità fisica** (quante unità compatibili esistono).

> ⚠️ **Implicazione importante da validare:** se l'**acquisto** di mandrini/tastatori/maschere è
> illimitato, immediato e gratuito, allora quei tre tipi **non sono mai un collo di bottiglia**
> (se ne può sempre aggiungere uno). In tal caso il vincolo reale di risorsa ricade quasi
> esclusivamente su **teste e generatori** (unità fisiche limitate, model-specific).
> Va deciso (domanda D1 in §11) se l'acquisto è davvero illimitato o se l'inventario è fisso/limitato.

Le **preferenze** trasferimento-vs-acquisto hanno effetto pratico solo se si vuole produrre, in
output, un piano di movimentazione/acquisti. In tal caso restano come criterio di spareggio (§7).

---

## 9. Calcolo del ritardo: giorni lavorativi vs settimane di calendario

- Schedulazione e durate avanzano in **giorni lavorativi**.
- La fine dell'ultima attività si converte in una **data di calendario reale**.
- Il **ritardo** = differenza in **giorni di calendario** tra quella data e la `due_date`.
- Le **settimane** = ⌈ ritardo / 7 giorni di calendario ⌉.

Serve quindi una funzione di **mappatura indice-giorno-lavorativo → data di calendario** per fare il
confronto con la due date. (Festività ignorate per ora.)

---

## 10. Approccio risolutivo consigliato

1. **Motore principale: Constraint Programming — CP-SAT (Google OR-Tools).**
   Ideale per scheduling con intervalli, non sovrapposizione su risorse, assegnamento flessibile
   (più risorse compatibili per requisito), precedenze e obiettivo di ritardo pesato.
   - Attività → intervalli `[inizio, inizio + durata]`.
   - Assegnamento risorsa → intervalli **opzionali** per coppia (attività, risorsa compatibile) +
     vincolo "esattamente quantità richiesta".
   - Capacità unitaria → `NoOverlap` per ogni risorsa.
   - Obiettivo lessicografico (penale, poi spareggi).

2. **Alternativa: MILP** (modellazione classica, più pesante sui vincoli di non sovrapposizione).

3. **Euristica di supporto** (per istanze grandi o warm start):
   regola **EDD** (Earliest Due Date) + assegnamento greedy delle risorse compatibili, poi
   raffinamento con CP-SAT entro un time limit.

---

## 11. Domande residue (poche, ma una è importante)

- **D1 (importante):** l'**inventario** delle risorse è **fisso** (solo quello iniziale degli
  stabilimenti) oppure è ammesso **acquistare** nuove unità senza limiti? Questo determina se
  mandrini/tastatori/maschere possono mai essere un collo di bottiglia (§8).
- **D2:** "settimana" di ritardo = **7 giorni di calendario** (assunto) confermato?
- **D3:** serve in **output** anche il piano di trasferimenti/acquisti (con le preferenze), oppure
  basta lo schedule con l'assegnamento delle risorse?
- **D4:** stato iniziale all'istante 0 (attività già in corso, risorse già impegnate, job già in ritardo) — esiste?

> Default proposti se non rispondi: D1 = inventario fisso (nessun acquisto), D2 = 7 giorni di
> calendario, D3 = solo schedule + assegnamento, D4 = nessuno stato iniziale.

---

## 12. Output attesi del sistema

- **Schedule**: per ogni attività → data inizio, data fine, stabilimento, risorse assegnate.
- **Per ogni job**: data di consegna prevista, giorni/settimane di ritardo, penale.
- **Riepilogo KPI**: penale totale, numero di job in ritardo, (opz.) trasferimenti/acquisti.
- **Diagnostica**: per i job in ritardo, indicazione del collo di bottiglia (risorsa scarsa,
  precedenza, earliest start).

---

## 13. Casi limite da gestire

- Job **già impossibile** da consegnare in tempo → minimizzare comunque la penale (no infeasibility).
- **Contesa** su una risorsa scarsa (es. unica testa di un modello) tra più job → sequenziamento.
- **Nessuna risorsa con `bore = mid`** → usare ammissibile con penalità di spareggio.
- Attività il cui **earliest_start** da solo causa il ritardo (collo di bottiglia non risorsale).
- Catena lunga la cui **somma durate** supera il tempo disponibile prima della due date.
- **Pareggi** tra schedule a parità di penale → spareggio su preferenze.
- **Orizzonte** troppo corto → ampliarlo o segnalare.

---

## 14. Piano di sviluppo a fasi

**Fase 0 — Setup e dati**
- Definire il **modello dati** (Job, Activity, Plant, Resource) e i formati di input/output.
- Implementare il **calendario lavorativo** (lun–ven) e la mappatura giorno-lavorativo ↔ data.
- Validazione input (es. coerenza ordine attività, attributi/quantità, risorse esistenti).

**Fase 1 — Generazione dei requisiti**
- Da ogni attività, derivare i requisiti in base agli attributi presenti (§5) e alle quantità.
- Pre-calcolare gli insiemi di **risorse compatibili** per ogni requisito (bore-range, tube_pitch,
  modello_testa), inclusa la coerenza testa–generatore.

**Fase 2 — Modello di ottimizzazione (CP-SAT)**
- Variabili: intervalli attività; assegnamenti opzionali attività–risorsa.
- Vincoli: precedenza in catena, earliest start, copertura quantità, non sovrapposizione risorse,
  coerenza testa–generatore.
- Calcolo ritardo/penale (conversione a settimane, arrotondamento per eccesso).

**Fase 3 — Obiettivo e risoluzione**
- Obiettivo lessicografico: (1) penale totale, (2) preferenze di spareggio.
- Time limit, gestione soluzione incumbent, parametri del solver.

**Fase 4 — Euristica e robustezza**
- Euristica EDD + greedy per warm start e per istanze grandi.
- Test su casi limite (§13).

**Fase 5 — Output e reporting**
- Generazione schedule, KPI, diagnostica colli di bottiglia.
- (Opz.) piano trasferimenti/acquisti se richiesto (D3).

**Fase 6 — Validazione**
- Confronto su istanze reali/storiche; verifica che le penali calcolate siano corrette;
- tuning dei pesi di spareggio.

---

*Per procedere allo sviluppo è utile (ma non bloccante) rispondere alle domande D1–D4 del §11.*
