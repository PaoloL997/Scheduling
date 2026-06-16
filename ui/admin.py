from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from ui import models


@admin.register(models.Stabilimento)
class StabilimentoAdmin(admin.ModelAdmin):
    """Admin for production plants."""

    search_fields = ("nome",)


class UserProfileInline(admin.StackedInline):
    """Edit plant assignment directly on the user admin page."""

    model = models.UserProfile
    can_delete = False
    extra = 0
    max_num = 1
    fields = ("stabilimento",)


class UserAdmin(BaseUserAdmin):
    """Extend the built-in user admin with profile fields."""

    inlines = (UserProfileInline,)


admin.site.unregister(User)
admin.site.register(User, UserAdmin)

admin.site.register(models.Attivita)
admin.site.register(models.Responsabile)
admin.site.register(models.RisorsaAttivita)
admin.site.register(models.Job)
admin.site.register(models.TestaItem)
admin.site.register(models.MandrinoItem)
admin.site.register(models.TastatoreItem)
admin.site.register(models.MascheraItem)
admin.site.register(models.GeneratoreItem)
admin.site.register(models.MppSyncQueue)
admin.site.register(models.ConfigurazioneTesta)
admin.site.register(models.GeneratoreCompatibile)
admin.site.register(models.ProblemaAttivita)
admin.site.register(models.RisoluzioneProblema)
