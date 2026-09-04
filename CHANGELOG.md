# Changelog

## 2026-09-04

### Added

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
