# update_attivita stores a null item as "None" and "false" as True

ui/services/bridge_write.py `update_attivita`: `item_value = str(data["item"]).strip()` turns a JSON `"item": null` into the literal item `"None"`, which passes the empty check and is saved; `a.tp60_custom = bool(data["tp60_custom"])` turns the string `"false"` into `True`. Refuse a null item with the existing 400 ("Il valore di item è obbligatorio.") and parse the flag with the same rule as `_parse_bool` in ui/services/inventory.py.
