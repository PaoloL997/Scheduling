"""URL patterns for the ui application."""

from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from ui import views, views_attivita, views_auth, views_import, views_ottimizzazione

urlpatterns = [
    path("", views.index, name="index"),
    path("pianificazione/", views.pianificazione, name="pianificazione"),
    path("inventario/", views.inventario, name="inventario"),
    path(
        "ottimizzazione/",
        views_ottimizzazione.ottimizzazione,
        name="ottimizzazione",
    ),
    path(
        "ottimizzazione/parametri/",
        views_ottimizzazione.ottimizzazione_parametri,
        name="ottimizzazione_parametri",
    ),
    path(
        "ottimizzazione/esegui/",
        views_ottimizzazione.ottimizzazione_run,
        name="ottimizzazione_run",
    ),
    path(
        "ottimizzazione/risultati/",
        views_ottimizzazione.ottimizzazione_risultati,
        name="ottimizzazione_risultati",
    ),
    path(
        "ottimizzazione/nuova/",
        views_ottimizzazione.ottimizzazione_nuova,
        name="ottimizzazione_nuova",
    ),
    path(
        "api/ottimizzazione/solve/",
        views_ottimizzazione.api_ottimizzazione_solve,
        name="api_ottimizzazione_solve",
    ),
    path(
        "api/ottimizzazione/spiegazione/",
        views_ottimizzazione.api_ottimizzazione_spiegazione,
        name="api_ottimizzazione_spiegazione",
    ),
    path(
        "api/ottimizzazione/accetta/",
        views_ottimizzazione.api_ottimizzazione_accetta,
        name="api_ottimizzazione_accetta",
    ),
    path("api/status/", views.api_status, name="api_status"),
    path("api/heartbeat/", views.api_heartbeat, name="api_heartbeat"),
    path(
        "api/inventario/import-excel/",
        views_import.api_inventario_import_excel,
        name="api_inventario_import_excel",
    ),
    path(
        "api/inventario/<str:table>/",
        views.api_inventario_add,
        name="api_inventario_add",
    ),
    path(
        "api/inventario/<str:table>/<str:uid>/",
        views.api_inventario_detail,
        name="api_inventario_detail",
    ),
    path(
        "api/attivita/analizza/",
        views_attivita.api_analizza_wps,
        name="api_analizza_wps",
    ),
    path(
        "api/attivita/risorse/", views_attivita.api_get_risorse, name="api_get_risorse"
    ),
    path(
        "api/attivita/salva/",
        views_attivita.api_salva_attivita,
        name="api_salva_attivita",
    ),
    path(
        "api/attivita/heatmap/",
        views_attivita.attivita_heatmap_data,
        name="attivita_heatmap_data",
    ),
    path(
        "api/attivita/<int:pk>/problemi/",
        views.api_attivita_problemi,
        name="api_attivita_problemi",
    ),
    path(
        "api/attivita/problemi/<int:pk>/risolvi/",
        views.api_problema_risolvi,
        name="api_problema_risolvi",
    ),
    path("api/tasks/update/", views.api_tasks_update, name="api_tasks_update"),
    path("api/jobs/create/", views.api_jobs_create, name="api_jobs_create"),
    path("api/jobs/check/", views.api_jobs_check, name="api_jobs_check"),
    path("api/app-settings/", views.api_app_settings, name="api_app_settings"),
    path("api/tasks/delete/", views.api_attivita_delete, name="api_attivita_delete"),
    path("api/jobs/update/", views.api_jobs_update, name="api_jobs_update"),
    path("api/jobs/delete/", views.api_jobs_delete, name="api_jobs_delete"),
    path("api/gantt/", views.api_gantt, name="api_gantt"),
    path(
        "api/commesse/deadline/",
        views.api_commesse_deadline,
        name="api_commesse_deadline",
    ),
    path("impostazioni/", views.impostazioni, name="impostazioni"),
    path(
        "api/stabilimenti/",
        views.api_stabilimenti_create,
        name="api_stabilimenti_create",
    ),
    path(
        "api/stabilimenti/<int:pk>/",
        views.api_stabilimenti_detail,
        name="api_stabilimenti_detail",
    ),
    path(
        "api/responsabili/",
        views.api_responsabili_create,
        name="api_responsabili_create",
    ),
    path(
        "api/responsabili/<int:pk>/",
        views.api_responsabili_detail,
        name="api_responsabili_detail",
    ),
    path(
        "api/config-testa/",
        views.api_config_testa_create,
        name="api_config_testa_create",
    ),
    path(
        "api/config-testa/<int:pk>/",
        views.api_config_testa_detail,
        name="api_config_testa_detail",
    ),
    path(
        "api/config-testa/<int:config_pk>/generatori/",
        views.api_gen_compatibile_create,
        name="api_gen_compatibile_create",
    ),
    path(
        "api/config-testa/generatori/<int:pk>/",
        views.api_gen_compatibile_detail,
        name="api_gen_compatibile_detail",
    ),
    path("login/", views_auth.login_view, name="login"),
    path("register/", views_auth.register_view, name="register"),
    path("logout/", views_auth.logout_view, name="logout"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
