# Pianificazione view queries each reference table twice

ui/views.py `pianificazione`: `stabilimenti`, `responsabili` and `tipi_attivita` are built as `sorted(set(orm_list + get_xxx()))`, but `get_stabilimenti`, `get_responsabili` and `get_tipi_attivita` in ui/services/activity_inventory.py read the same three models, so each list is merged with itself at the cost of three extra queries per page load; the docstring still says the dropdowns come from "ORM-managed and imported sources". Use each helper once (keeping the `nome`/`colore` query needed for `plant_colors`) and fix the docstring.
