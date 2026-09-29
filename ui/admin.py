from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from ui import models
from ui.permissions import sync_user_flags


@admin.register(models.Stabilimento)
class StabilimentoAdmin(admin.ModelAdmin):
    """Admin for production plants."""

    search_fields = ("nome",)


@admin.register(models.UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """Standalone list to review and reassign permission layers in bulk."""

    list_display = ("user", "stabilimento", "ruolo")
    list_editable = ("ruolo",)
    list_filter = ("ruolo", "stabilimento")
    search_fields = ("user__username", "user__first_name", "user__last_name")
    list_select_related = ("user", "stabilimento")


class UserProfileInline(admin.StackedInline):
    """Edit plant assignment and permission layer on the user admin page."""

    model = models.UserProfile
    can_delete = False
    extra = 0
    max_num = 1
    fields = ("stabilimento", "ruolo")
    verbose_name_plural = "Profilo e permessi"


class UserAdmin(BaseUserAdmin):
    """Extend the built-in user admin with profile fields.

    ``ruolo`` is the single control for access: saving it propagates to
    ``is_staff`` / ``is_superuser`` via
    :func:`ui.permissions.sync_user_flags`, so the admin never has to touch
    the Django flags directly.
    """

    inlines = (UserProfileInline,)
    list_display = BaseUserAdmin.list_display + ("ruolo",)
    list_select_related = ("profile",)

    @admin.display(description="Ruolo", ordering="profile__ruolo")
    def ruolo(self, obj) -> str:
        """Return the readable permission layer of the listed user."""
        profile = getattr(obj, "profile", None)
        if profile is None:
            return "—"
        return profile.get_ruolo_display()

    def save_related(self, request, form, formsets, change) -> None:
        """Re-sync the staff flags after the inline profile has been saved."""
        super().save_related(request, form, formsets, change)
        profile = getattr(form.instance, "profile", None)
        if profile is not None:
            profile.refresh_from_db()
            sync_user_flags(profile)


admin.site.unregister(User)
admin.site.register(User, UserAdmin)

admin.site.register(models.Attivita)
admin.site.register(models.Responsabile)
admin.site.register(models.TipoAttivita)
admin.site.register(models.RisorsaAttivita)
admin.site.register(models.Job)
admin.site.register(models.TestaItem)
admin.site.register(models.MandrinoItem)
admin.site.register(models.TastatoreItem)
admin.site.register(models.MascheraItem)
admin.site.register(models.GeneratoreItem)
admin.site.register(models.ConfigurazioneTesta)
admin.site.register(models.GeneratoreCompatibile)
admin.site.register(models.ProblemaAttivita)
admin.site.register(models.RisoluzioneProblema)
