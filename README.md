# Production Scheduling

A Django web application for planning and tracking a production schedule, backed by a PostgreSQL database.

## Overview

Activities, commesse, resource assignments and the inventory live in PostgreSQL. Planners work in the browser, operators on tablets through the JSON bridge API, and every change is written straight to the database: nothing is imported from or written back to external files.

## Key concepts

### Data
| Table | Content |
|------|---------|
| `attivita`, `risorse_attivita` | Activities and the resources assigned to them |
| `attivita_predecessori` | Precedences between activities of the same commessa |
| `job` | Commesse: cost, penalty, delivery date |
| `testa_item`, `mandrino_item`, `tastatore_item`, `maschera_item`, `generatore_item` | Inventory |
| `configurazione_testa`, `generatore_compatibile` | Per-testa requirements and compatible generators |
| `problema_attivita`, `operation_log` | Operational problems and audit trail |

See [docs/dataset.md](docs/dataset.md) for the data lifecycle.

Every activity belongs to a `job` row through a foreign key: saving an
activity for a commessa that has no `job` yet creates a bare one, and a
commessa cannot be renamed. Predecessors are a many-to-many relation, so
renaming an activity never breaks the links that point at it. Clients keep
exchanging `commessa` and `predecessors` as strings (`activity_id`s joined by
`", "`); `ui/services/precedences.py` resolves them within the commessa.

### Architecture
```
browser ──┐
          ├─► Django (ui/: views, services, bridge API) ──► PostgreSQL
tablet  ──┘
```

The "Importa da Excel" button in Inventario bulk-loads resources from an `.xlsx` file into the database; it is an upload tool, not a data source.

### WPS integration
Activities can have a WPS (Welding Procedure Specification) PDF attached. The app sends it to the **OpenAI Responses API** (`gpt-5.6-sol` by default, set `OPENAI_WPS_MODEL` in `.env` to change it) to extract bore dimensions and tube pitch, pre-filling the resource selection step of the activity wizard. The answer is constrained by a strict JSON schema, and the extraction is best-effort: when it fails the wizard says why and the planner types the measurements by hand.

### TP60 Custom (Sporgenza Tubo-Piastra)

Below the WPS measurements the wizard shows a **TP60 Custom (Sporgenza Tubo-Piastra)** flag, saved on `Attivita.tp60_custom`. Such workings need a fixed set of accessories, whatever the testa configuration says: **2 mandrini and 1 maschera, no tastatore**. Those are the minimums used for pre-selection and for `acquisto` problems; leaving the tastatore unselected raises no problem, and its list stays populated so the planner can still tick one by hand. Teste and generatori keep their configured minimums.

## Access layers

Every account carries one of three permission layers, stored on `UserProfile.ruolo`:

| Layer | Can view | Can add/edit commesse, attività, inventory, settings | Admin panel |
|-------|----------|------------------------------------------------------|-------------|
| `admin` | ✅ | ✅ | ✅ |
| `scrittura` | ✅ | ✅ | ❌ |
| `lettura` | ✅ | ❌ | ❌ |

New accounts start as `lettura`. An admin assigns the layer from the Django admin panel
(`/admin/`), either inline on the user page or from the **Profilo e permessi** list; saving
the role syncs `is_staff` / `is_superuser` so only admins can reach the panel.

Enforcement lives in `ui/permissions.py`: the `write_required` / `admin_required`
decorators wrap every mutating view (web app and tablet bridge) and answer HTTP 403 —
with a JSON body for `/api/` endpoints. Templates additionally hide the controls a
read-only account cannot use.

## Resource occupancy

`ui/services/resource_conflicts.occupation_window` is the single definition of
when a resource is held:

| Activity state | Occupies |
|---|---|
| planned only | `[data_inizio, data_fine]` |
| in progress | `[actual_start, max(data_fine, effective_finish, today)]` |
| completed | `[actual_start or data_inizio, actual_finish]` |

`effective_finish` lays the planned duration, counted in **working days**
(Monday to Friday), out from `actual_start`. A running activity holds its
resources through today however late it is: nothing is released until
`actual_finish` is recorded.

Overlap detection, reassignment and availability lists all read that one
function; nothing re-implements it.  The **Modifica risorse** picker included:
for an activity already under way it asks what is free from `actual_start`, not
from the planned `data_inizio`, so it offers exactly what the save will accept
— a unit released on the planned start day is no longer hidden.

**Facts are never refused.** Writing `actual_start` / `actual_finish` records
something that already happened, so the save always succeeds. A resulting
conflict with a *planned* activity becomes a `sovrapposizione` note on that
activity, plus a reschedule proposal. Only a real change to the planned dates or
to the assigned resources can be rejected with a 409 — plus the three rules
below on starting an activity, which are the only things that can refuse a
start: the minimum kit must be booked, no piece may be held by an activity in
progress, and the start cannot be dated in the future.

## The effective start is never in the future

`actual_start` records a fact, so it cannot carry the date of a day that has
not come: the operator may write **today or any past date**, never tomorrow.
`ui.services.bridge_write.validate_actual_start_not_future` is the single
definition of the rule; `/api/tasks/update/` and the tablet bridge both reach it
through `update_attivita` and answer **400** with the two dates spelled out.

Only a *new* value is checked, so an activity that already carries a future
`actual_start` — imported from MS Project before the rule existed — stays
editable, and correcting it to today or to a past date always works. Planned
dates (`start` / `finish`) are unaffected: planning ahead is the point.

In Pianificazione the **Inizio effett.** date picker carries `max` = today, and
the panel repeats the check before the round-trip.

## Minimum resources to start

An activity cannot be launched — `actual_start` cannot be recorded — before its
minimum kit is booked: **one testa, one generatore** and, per testa model, the
minimum of each accessory from `ConfigurazioneTesta` (every minimum defaults to
`1` when the model has no configuration row).

`ui/services/problem_detection.resource_shortfalls` is the single definition of
"below the minimum": it raises the `acquisto` notes *and* blocks the start, so
an activity can be started exactly when it carries no open shortfall.
`update_attivita` guards only the transition to started, so editing an activity
that was already running is never refused. The refusal is a 409 whose body
carries `missing_resources` (category, label, `selezionati`, `richiesti`) on
both `/api/tasks/update/` and the tablet bridge.

Every activity payload also carries `min_requirements` and `can_start`, so
Pianificazione warns on the **Inizio effett.** field and stops the save before
the round-trip.

## Starting with pieces another activity holds

Recording `actual_start` tests the window the activity will really occupy
(from `actual_start` through `max(data_fine, effective_finish, today)`)
against every other booking of its pieces:

- **Held by a planned activity** → the start is saved; the response carries
  `warnings` and a `warning_message` saying the pieces were meant for another
  activity in that window. The overlap engine then re-equips the planned
  activity with compatible free pieces or leaves it a reschedule proposal.
- **Held by an activity in progress** (started, not closed) → 409 with
  `error` and `conflicts`; nothing is saved. The piece is released only by that
  activity's `actual_finish`.
- A closed activity has released its pieces and never blocks.

Both `/api/tasks/update/` and the tablet bridge answer the same way.

An activity that was never started and is already past `data_fine` raises a
`ritardo` note, shows a **NON INIZIATA · SCADUTA** badge, and gets a reschedule
proposal. It keeps its bookings until the proposal is accepted, so no one else
can quietly take the resource.

### Availability column

Every inventory table carries a **Disponibilità** dot: green when no occupancy
window contains today, red when one does. Hovering it opens the detail — a red
dot names the activities holding the item and the day it comes free, a green one
how many days it stays free and which activities take it next. Back-to-back
bookings are followed, so the release day is the first genuinely idle one.

A testa or generatore in maintenance is red whatever its bookings: the unit
cannot be used, so `in_manutenzione` overrides the occupancy and the tooltip
says so. Toggling maintenance (or saving the detail form) answers with the
recomputed descriptor, so the dot repaints without a reload.

`ui/services/resource_availability.py` builds both states from the same
`occupation_window`, through
`resource_conflicts.bookings_not_ended`.

## Resource overlaps

When a late start makes two activities share a resource:

1. **Automatic swap** — if a compatible free item exists, the blocked (not-started) activity is reassigned and a resolved note is stored.
2. **Reschedule proposal** — if no substitute exists, the CP-SAT model is run on that activity plus its precedence-connected chain. Every other activity's resource bookings are treated as fixed reservations. Planning shows the proposal (dates, assigned resources, mini-Gantt, deadline badge) with **Accept** / **Reject**.

Overlap warnings on the home Gantt open the same notes modal.

## When notes are reconciled

Every operational note (`acquisto`, `spostamento`, `sovrapposizione`,
`ritardo`) is derived from the activities, their assignments and the
calendar. **Reading never writes**: Pianificazione, the Gantt, the kanban and
the tablet bridge display what the last write left behind, through the pure
read layer in `ui/services/planning_read.py`.

The write paths reconcile when they are done, through
`ui/services/reconcile.py`: saving an activity from the wizard, editing it
(web or tablet), deleting an activity or a commessa, a tablet scan, accepting
an optimization or a reschedule proposal, and inventory changes.

`ritardo` is the one note that changes with the calendar alone, so the pages
sync it once a day per process, and a scheduler should run the full pass
nightly:

```bash
python manage.py reconcile_problems
```

## Optimization engine

`src/optimization/` is a CP-SAT model over working days, independent of
Django; `ui/services/optimization_problem.py` builds its input from the
database (activities, the minimum kit of each activity, the inventory and the
testa/generator compatibility) and `ui/services/optimization_apply.py` writes
an accepted proposal back.

The objective is lexicographic: total weekly penalty first (every commessa
counts, one without a penalty weighs one cent per late week), then total late
days, then the bore-mid preference, resource continuity along precedences and
workload levelling. `SOLVER_TIME_LIMIT` (seconds, default 10) is the budget
for the whole run; the primary phase gets the largest share and, when it finds
no schedule in it, the whole remainder.

## Tablet bridge

`/api/bridge/...` is the JSON channel for tablets. Reads answer
`{"ok": true, "data": ...}` from the shared read layer. Writes run the web
application's own view functions on the bridge request, so rules, audit
entries (`source: tablet`) and response bodies are the same on both channels.

## Tablet scan and compatible accessories

The tablet starts an activity by scanning its components against
`POST /api/bridge/attivita/<pk>/scan/` (write permission required):

```json
{"tipo": "mandrino", "codice": "MND-117", "atteso": "MND-102", "conferma": false}
```

- **Teste and generatori** must match the assigned UID exactly; anything else
  is refused with `motivo: "non_corrispondente"`.
- **Mandrini, tastatori and maschere** are checked by measure, with the same
  rule as the planning picker (`ui/services/measures.py`): bore within
  `bore_min`–`bore_max`, or mask pitch equal to `tube_pitch`. Out of
  tolerance → 409 `non_compatibile` with the measures spelled out. The activity
  must define the requirement (`requisiti`) and the item measure must be
  readable (`misura`): an unreadable value is never treated as zero.
- **Any item held by another activity in progress** (started, no actual
  finish) → 409 `occupata`, whether it is the planned piece or not and
  whatever the dates: it is in use until that activity is closed.
- A different accessory is then classified in the activity's occupation window:
  1. **Free** → allowed; a different piece needs `conferma: true` after the
     operator sees `"Stai usando MND-117 al posto di MND-102, va bene?"`.
  2. **Held by an activity closed in the window** → 409 `occupata`, nothing
     changes.
  3. **Held only by planned activities** → same confirmation; on confirm the
     started activity gets the scanned piece, each robbed activity is
     re-equipped with a compatible free spare through the overlap engine
     (`pick_free_spare`), and when none exists the piece is removed there so
     the standard `acquisto` warning appears in planning. Every step leaves a
     resolved `sostituzione` note on both sides.

Without `conferma` nothing is written: the response carries
`richiede_conferma`, `messaggio` and `attivita_sottratte`. `atteso` is inferred
when the activity holds a single item of that category and required otherwise.

Resource updates through `update_attivita` (web and bridge) replace the full
assignment list, but a category **absent** from `risorse` keeps its current
assignments; only an explicit empty list clears it.

## Demo seeds

```bash
python manage.py seed_overlap_demo              # late overlap with a spare testa (auto-swap)
python manage.py seed_overlap_demo --reschedule # same delay, unique testa → reschedule proposal
python manage.py seed_overlap_demo --clear
python manage.py seed_overlap_demo --overdue    # commessa 25078D: close late + never-started overdue

python manage.py seed_optimization_demo         # penalty + load-balancing commesse
python manage.py seed_optimization_demo --clear
```

`--reschedule` creates commesse **R301** (late Saldatura) and **R305** (blocked Montaggio + Collaudo/Imballo chain, independent Documentazione). Open Planning → R305 → Notes to accept or reject.

The seeds write to the configured database and stay there until removed with `--clear`: run them only against a development database.

## Requirements

- Python 3.13+
- Poetry
- PostgreSQL 14+

## Setup

Create the database role and the database (the role needs `CREATEDB` to run the tests):

```sql
CREATE ROLE scheduling LOGIN CREATEDB PASSWORD '<password>';
CREATE DATABASE scheduling OWNER scheduling ENCODING 'UTF8' TEMPLATE template0;
```

Then:

```bash
poetry install
cp .env.example .env   # set DB_PASSWORD (and DB_* if not local), OPENAI_API_KEY
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

| `.env` key | Default | Meaning |
|------------|---------|---------|
| `DB_NAME`, `DB_USER`, `DB_PASSWORD` | — (required) | Database, role and password |
| `DB_HOST`, `DB_PORT` | `localhost`, `5432` | Server address |
| `DB_CONN_MAX_AGE` | `60` | Seconds a connection is reused (0 = one per request) |

Tests run against the same server, on a `test_<DB_NAME>` database:

```bash
python manage.py test ui
pytest src/optimization/tests
```

### Backups

The database is the only copy of the data. Back it up regularly, for example:

```bash
pg_dump -Fc -U scheduling -d scheduling -f scheduling-YYYYMMDD.dump
pg_restore -U scheduling -d scheduling --clean scheduling-YYYYMMDD.dump
```
