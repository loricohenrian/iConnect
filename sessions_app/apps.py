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
