# Operations log shows UTC instead of local time

ui/views.py `_serialize_log_entry`: `entry.timestamp.strftime("%d/%m/%Y %H:%M")` formats an aware UTC datetime (`USE_TZ = True`, `TIME_ZONE = "Europe/Rome"` in config/settings.py), so every row of Impostazioni → Log is one or two hours behind the wall clock. Format `django.utils.timezone.localtime(entry.timestamp)` instead.
