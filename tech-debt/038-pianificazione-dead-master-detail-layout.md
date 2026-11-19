# Dead master/detail layout code in pianificazione.html

ui/templates/ui/pianificazione.html, old master/detail layout: `_restoreDetachedActivities`, `_renderSelectedPianJob`, `selectPianJob`, `editSelectedPianJob`, `openAddModalForSelectedJob`, `openAddModalForJob`, `clearPianSearch` and the `_selectedPianCommessa` state are reachable from no markup or caller outside their own group, and the ids they read (`pian-detail-body`, `pian-kpi-total`, `pian-jobs-count`, `pian-edit-job-btn`) do not exist in the template. Delete them, checking each name with a search across ui/ before removing it.
