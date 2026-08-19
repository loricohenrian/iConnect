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

from celery.schedules import crontab

app.conf.beat_schedule = {
    'check-expired-sessions-every-minute': {
        'task': 'sessions_app.tasks.check_expired_sessions',
        'schedule': 60.0,  # Run every 60 seconds
    },
    'expire-voucher-codes-every-5-minutes': {
        'task': 'sessions_app.tasks.expire_voucher_codes',
        'schedule': 300.0,
    },
}

@worker_ready.connect
def on_worker_ready(**kwargs):
    """Restore iptables rules for active/paused sessions after reboot."""
    from sessions_app.tasks import restore_iptables_on_boot
    restore_iptables_on_boot.delay()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')

