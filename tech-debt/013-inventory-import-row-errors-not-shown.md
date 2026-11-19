# Excel import never shows the rejected rows

ui/templates/ui/inventario.html `submitImport`, `showImportBanner` and `showImportResult`: when `/api/inventario/import-excel/` imports part of a file it returns the rejected rows in `errors`, but the page only shows "✓ Importate N risorse…" and reloads after two seconds; the branch of `showImportResult` that lists `res.errors` is unreachable because the function is only ever called with `type: 'error'`. When `data.errors` is not empty, show the list and skip the automatic reload.
