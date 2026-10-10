from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dashboard'
    verbose_name = 'Admin Dashboard'

    def ready(self):
        from . import notification_signals  # noqa: F401
