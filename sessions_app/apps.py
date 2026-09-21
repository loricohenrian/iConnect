import logging
import os
import sys

from django.apps import AppConfig
from django.conf import settings


logger = logging.getLogger(__name__)


class SessionsAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sessions_app'
    verbose_name = 'Session Management'

    def ready(self):
        # Configure SQLite PRAGMAs safely via connection_created signal
        from django.db.backends.signals import connection_created

        def configure_sqlite_pragmas(sender, connection, **kwargs):
            if connection.vendor == 'sqlite':
                try:
                    with connection.cursor() as cursor:
                        cursor.execute('PRAGMA journal_mode=WAL;')
                        cursor.execute('PRAGMA synchronous=NORMAL;')
                        cursor.execute('PRAGMA busy_timeout=30000;')
                except Exception as e:
                    logger.warning('Failed to apply SQLite PRAGMAs: %s', e)

        connection_created.connect(configure_sqlite_pragmas)

        if not getattr(settings, 'PISONET_ENFORCE_FIREWALL_BASELINE_ON_STARTUP', True):
            return

        argv_blob = ' '.join(sys.argv).lower()
        server_markers = ('runserver', 'gunicorn', 'uwsgi', 'daphne')
        is_server_process = any(marker in argv_blob for marker in server_markers)
        if not is_server_process:
            return

        # runserver launches a parent process for autoreload; enforce only in child.
        if 'runserver' in argv_blob and os.environ.get('RUN_MAIN') != 'true':
            return

        from . import iptables

        if not iptables.enforce_firewall_baseline():
            logger.error('Firewall baseline enforcement failed during startup')
