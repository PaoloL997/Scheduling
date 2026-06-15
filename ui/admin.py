from django.contrib import admin

from ui import models

admin.site.register(models.Attivita)
admin.site.register(models.Stabilimento)
admin.site.register(models.Responsabile)
admin.site.register(models.RisorsaAttivita)
admin.site.register(models.Job)
admin.site.register(models.TestaItem)
admin.site.register(models.MandrinoItem)
admin.site.register(models.TastatoreItem)
admin.site.register(models.MascheraItem)
admin.site.register(models.GeneratoreItem)
admin.site.register(models.AppSettings)
admin.site.register(models.MppSyncQueue)
admin.site.register(models.ConfigurazioneTesta)
admin.site.register(models.GeneratoreCompatibile)
admin.site.register(models.ProblemaAttivita)
admin.site.register(models.RisoluzioneProblema)
