# CSS blocks for classes used nowhere

ui/static/ui/style.css: these rule blocks style class names that appear in no template, script or Python file: `.step-indicator` / `.step-dot*`, `.pian-layout`, `.pian-detail-head`, `.pian-kpi-row`, `.prob-chip*`, `.prob-panel-*`, `.heatmap-legend`, `.settings-generali-*`, `.recap-card*`, `.wps-data-*`, `.bore-calc-*`, `.ott-param-*`, `.ott-res-summary*`. Delete them. Do not touch classes built at runtime from a prefix (`status-pill--*`, `log-badge--*`, `inv-avail--*`, `user-role-badge--*`).
