# A non-numeric orm_id gives a 500

ui/views.py `api_tasks_update` and `api_attivita_delete`: `int(orm_id)` is called on the request value without a guard, so `{"orm_id": "abc"}` raises `ValueError` and the client (browser or tablet bridge, which reuses these views) gets a 500 instead of a 400. Parse the id once in a small helper that answers 400 "orm_id non valido." for anything that is not a positive integer, and use it in both views.
