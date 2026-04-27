"""
Celery configuration for iConnect project.
"""
import os
from celery import Celery
from celery.signals import worker_ready

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pisowifi.settings')

app = Celery('pisowifi')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


@worker_ready.connect
def on_worker_ready(**kwargs):
    """Restore iptables rules for active/paused sessions after reboot."""
    from sessions_app.tasks import restore_iptables_on_boot
    restore_iptables_on_boot.delay()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')

