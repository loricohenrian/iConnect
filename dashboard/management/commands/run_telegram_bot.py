"""Private-admin Telegram polling daemon; credentials are never logged."""
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from dashboard.telegram_bot import (
    get_telegram_config, handle_telegram_command, telegram_config_ready,
)

logger = logging.getLogger(__name__)


def _internet_monitor_loop():
    """Retain the existing independent ISP health monitor."""
    while True:
        try:
            close_old_connections()
            from sessions_app.tasks import check_internet_status
            check_internet_status()
        except Exception as error:
            logger.error("ISP monitor failed (%s)", type(error).__name__)
        finally:
            close_old_connections()
        time.sleep(15)


def _credentials(cfg):
    return cfg.get('enabled'), cfg.get('token'), cfg.get('chat_id')


def _notification_loop():
    """Deliver durable alerts independently of session timer workers/polling."""
    while True:
        try:
            close_old_connections()
            from dashboard.telegram_notifications import deliver_pending_notifications
            delivered = deliver_pending_notifications()
            if delivered:
                logger.info('Delivered %s queued Telegram alert(s)', delivered)
        except Exception as error:
            logger.error('Telegram alert delivery unavailable (%s)', type(error).__name__)
        finally:
            close_old_connections()
        time.sleep(5)


def dispatch_updates(updates, cfg, offset, not_before):
    """Reject groups, stale queued commands, and changed/disabled credentials."""
    for update in updates:
        offset = max(offset, int(update.get('update_id', 0)) + 1)
        message = update.get('message') or {}
        sender = message.get('from') or {}
        chat = message.get('chat') or {}
        if (
            not isinstance(message.get('text'), str)
            or chat.get('type') != 'private'
            or str(sender.get('id')) != cfg['chat_id']
            or str(chat.get('id')) != cfg['chat_id']
            or sender.get('is_bot')
            or message.get('date', 0) < not_before
        ):
            continue
        current = get_telegram_config()
        if not telegram_config_ready(current) or _credentials(current) != _credentials(cfg):
            break
        try:
            handle_telegram_command(
                message['text'], sender['id'], sender.get('first_name', 'Operator'),
                chat_id=chat['id'],
            )
        except Exception as error:
            logger.error("Telegram command failed (%s)", type(error).__name__)
    return offset


class Command(BaseCommand):
    help = 'Run the private-admin Telegram bot with explicitly configured credentials'

    def handle(self, *args, **options):
        cfg = get_telegram_config()
        if not telegram_config_ready(cfg):
            raise CommandError('Telegram is disabled or missing a valid token/personal admin ID.')

        threading.Thread(target=_internet_monitor_loop, daemon=True).start()
        threading.Thread(target=_notification_loop, daemon=True).start()
        self.stdout.write('Telegram poller started for the configured private admin.')
        offset = 0
        credentials = _credentials(cfg)
        not_before = int(time.time())
        while True:
            try:
                close_old_connections()
                cfg = get_telegram_config()
                if not telegram_config_ready(cfg):
                    credentials = None
                    time.sleep(5)
                    continue
                if _credentials(cfg) != credentials:
                    credentials = _credentials(cfg)
                    offset = 0
                    not_before = int(time.time())
                params = urllib.parse.urlencode({
                    'offset': offset, 'timeout': 20,
                    'allowed_updates': json.dumps(['message']),
                })
                url = f"https://api.telegram.org/bot{cfg['token']}/getUpdates?{params}"
                request = urllib.request.Request(url, headers={'User-Agent': 'iConnectBot/1.0'})
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = json.load(response)
                if payload.get('ok'):
                    offset = dispatch_updates(payload.get('result', []), cfg, offset, not_before)
            except urllib.error.HTTPError as error:
                logger.warning('Telegram polling HTTP status %s', error.code)
                time.sleep(5)
            except Exception as error:
                logger.warning('Telegram polling unavailable (%s)', type(error).__name__)
                time.sleep(3)
            finally:
                close_old_connections()
