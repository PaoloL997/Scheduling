# Table and tipo maps duplicated outside resource_types

ui/services/inventory.py `_ALLOWED_TABLES`, `_TABLE_TO_TIPO`, `_MAINTENANCE_TABLES` and ui/services/resource_availability.py `TIPO_BY_TABLE`: ui/services/resource_types.py states that every mapping between tipo, categoria and table is "defined here and nowhere else", but these four constants re-list the same five tables, and `TIPO_TO_TABLE` in resource_types.py is referenced by nothing. Import `TABLES` / `TABLE_TO_TIPO` from resource_types in the two modules, delete the local copies and `TIPO_TO_TABLE`.
