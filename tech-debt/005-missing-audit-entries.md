# Three write endpoints leave no audit entry

ui/views_import.py `api_inventario_import_excel`, ui/views_ottimizzazione.py `api_ottimizzazione_accetta` and ui/views.py `_handle_deadline_update`: these three write endpoints never call `record_operation`, although ui/services/operation_log.py states that every human-facing mutating endpoint calls it exactly once. A bulk inventory import, an accepted optimization and a deadline change do not appear in Impostazioni → Log. Add one `record_operation` call to each (imported/skipped counts, number of activities updated, commessa and new deadline).
