# Production Scheduling

A Django web application for managing and visualising a production schedule stored in an MS Project (`.mpp`) file.

## Overview

The app bridges MS Project and a browser-based UI. It imports tasks and resources from a `.mpp` file at session start, lets users view and edit the schedule through the web interface, and writes all changes back to the `.mpp` file when the last user logs out.

## Key concepts

### Data sources
| File | Role |
|------|------|
| `dataset.mpp` | MS Project file — primary source for tasks, resources, and assignments |
| `jobs.xlsx` | Excel file — job metadata (cost, penalty, delivery date) |

Both files live outside version control and are managed by the application at runtime.

### Session lifecycle
1. **Server start** — file paths are configured; no files are touched.
2. **First login** — the `.mpp` and `.xlsx` are imported into the Django ORM; the `.mpp` is then held open via the MS Project COM API, locking it at the OS level.
3. **Active session** — changes made through the UI are persisted in `db.sqlite3` and queued in `MppSyncQueue`.
4. **Last logout** — queued changes are written back to the `.mpp` via COM, the file is saved, and the lock is released.

> The COM lock is in-process. Use a **single-worker** server (e.g. `waitress` or `runserver`); multi-process deployments are not supported.

### Architecture
```
dataset.mpp ─┐
             ├─► src/data/import.py ──► Django ORM (db.sqlite3)
jobs.xlsx   ─┘                              │
                                            ▼
                                    ui/ (views, templates)
                                            │
                                    MppSyncQueue
                                            │
                                            ▼
                                   src/data/project.py
                                   (MS Project COM API)
                                            │
                                            ▼
                                      dataset.mpp (write-back)
```

### WPS integration
Activities can have a WPS (Welding Procedure Specification) PDF attached. The app uses the **Gemini API** to extract bore dimensions and tube pitch from the PDF, pre-filling the resource selection step of the activity wizard.

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
| in progress | `[actual_start, max(data_fine, effective_finish)]` |
| completed | `[actual_start or data_inizio, actual_finish]` |

Overlap detection, reassignment and availability lists all read that one
function; nothing re-implements it.

**Facts are never refused.** Writing `actual_start` / `actual_finish` records
something that already happened, so the save always succeeds. A resulting
conflict becomes a `sovrapposizione` note on the activity that has not started
yet, plus a reschedule proposal. Only a real change to the planned dates or to
the assigned resources can be rejected with a 409.

An activity that was never started and is already past `data_fine` raises a
`ritardo` note, shows a **NON INIZIATA · SCADUTA** badge, and gets a reschedule
proposal. It keeps its bookings until the proposal is accepted, so no one else
can quietly take the resource.

## Resource overlaps

When a late start makes two activities share a resource:

1. **Automatic swap** — if a compatible free item exists, the blocked (not-started) activity is reassigned and a resolved note is stored.
2. **Reschedule proposal** — if no substitute exists, the CP-SAT model is run on that activity plus its precedence-connected chain. Every other activity's resource bookings are treated as fixed reservations. Planning shows the proposal (dates, assigned resources, mini-Gantt, deadline badge) with **Accept** / **Reject**.

Overlap warnings on the home Gantt open the same notes modal.

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
- The scanned item is then classified in the activity's occupation window:
  1. **Free** → allowed; a different piece needs `conferma: true` after the
     operator sees `"Stai usando MND-117 al posto di MND-102, va bene?"`.
  2. **Held by an activity in progress or closed** → 409 `occupata`, nothing
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

## Requirements

- Windows (MS Project COM API)
- MS Project installed
- Python 3.13+
- Poetry

## Setup

```bash
poetry install
cp .env.example .env   # add GEMINI_API_KEY
python manage.py migrate
python manage.py runserver
```
