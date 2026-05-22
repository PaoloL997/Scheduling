"""URL configuration for the programmazione_produzione project."""

from django.urls import include, path

urlpatterns = [
    path("", include("ui.urls")),
]
