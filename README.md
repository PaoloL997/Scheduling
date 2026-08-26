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

## Resource overlaps

When a late start makes two activities share a resource:

1. **Automatic swap** — if a compatible free item exists, the blocked (not-started) activity is reassigned and a resolved note is stored.
2. **Reschedule proposal** — if no substitute exists, the CP-SAT model is run on that activity plus its precedence-connected chain. Every other activity's resource bookings are treated as fixed reservations. Planning shows the proposal (dates, assigned resources, mini-Gantt, deadline badge) with **Accept** / **Reject**.

Overlap warnings on the home Gantt open the same notes modal.

## Demo seeds

```bash
python manage.py seed_overlap_demo              # late overlap with a spare testa (auto-swap)
python manage.py seed_overlap_demo --reschedule # same delay, unique testa → reschedule proposal
python manage.py seed_overlap_demo --clear

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
