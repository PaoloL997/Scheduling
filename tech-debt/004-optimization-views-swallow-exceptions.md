# Optimization views swallow exceptions without logging

ui/views_ottimizzazione.py `api_ottimizzazione_spiegazione` and `api_ottimizzazione_accetta`: the final `except Exception:` returns a generic 500 ("Errore durante l'applicazione della proposta.", "Errore durante la generazione della spiegazione.") without logging anything, so an unexpected failure while applying an accepted optimization leaves no trace. Add `logger.exception(...)` in both handlers.
