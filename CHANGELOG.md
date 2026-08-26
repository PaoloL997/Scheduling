# Changelog

## 2026-08-26

### Added

- Automatic **reschedule proposal** when a resource overlap cannot be resolved by swapping: the existing CP-SAT model is run on the blocked activity and its precedence chain; other activities reserve their resources. Planning notes show dates, assigned resources, a mini-Gantt, and Accept / Reject.
- `seed_overlap_demo --reschedule` demo (R301 late / R305 blocked chain).
- `ProblemaAttivita.proposta_json` to persist the proposal without re-solving on every Gantt load.

### Changed

- Wizard optimization now reserves resources of **all** activities outside the selected set (including planned, not-started ones), not only started/completed work.
- Overlap problems are attached only to the activity that has not started.
- Home Gantt always shows the warning badge for open problems; click opens the notes modal.
