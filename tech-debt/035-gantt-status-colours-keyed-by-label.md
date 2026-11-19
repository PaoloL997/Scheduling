# Gantt status colours are keyed by the display label

ui/static/ui/gantt.js `chartTheme().statusColors` and `JOB_STATUS_LABELS`: the colour of a commessa status is looked up by its display text (`theme.statusColors[stats.status]` with keys 'In tempo', 'Pianificata', 'In ritardo', 'Rischio penale'), so rewording a label in `JOB_STATUS_LABELS` silently drops its colour, and 'Non pianificata' has no entry. Key `statusColors` by the status key the server sends (`in_tempo`, `pianificata`, `in_ritardo`, `rischio_penale`, `non_pianificata`) and translate to the label only when writing the text.
