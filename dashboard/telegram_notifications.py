"""Retryable, leased delivery of support/security alerts by the Telegram daemon."""
from datetime import timedelta

from django.db.models import F, Q
from django.utils import timezone

from dashboard.models import TelegramNotification
from dashboard.telegram_bot import get_telegram_config, telegram_config_ready, send_telegram_message


def deliver_pending_notifications(limit=3):
    cfg = get_telegram_config()
    if not telegram_config_ready(cfg):
        return 0
    now = timezone.now()
    eligible = TelegramNotification.objects.filter(
        sent_at__isnull=True, cancelled_at__isnull=True, next_attempt_at__lte=now,
    ).filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
    ids = list(eligible.order_by('created_at', 'pk').values_list('pk', flat=True)[:limit])
    sent = 0
    for pk in ids:
        cfg = get_telegram_config()
        if not telegram_config_ready(cfg):
            break
        # Conditional UPDATE claims a lease atomically across processes. No DB lock
        # or customer-facing request is held while waiting on Telegram's network.
        claimed = eligible.filter(pk=pk).update(lease_until=now + timedelta(minutes=2), attempts=F('attempts') + 1)
        if not claimed:
            continue
        event = TelegramNotification.objects.get(pk=pk)
        flag = 'notify_tickets' if event.kind == 'ticket' else 'notify_security'
        if not cfg.get(flag):
            TelegramNotification.objects.filter(pk=pk).update(cancelled_at=timezone.now(), lease_until=None)
            continue
        try:
            success = send_telegram_message(event.body, parse_mode=None)
        except Exception:
            success = False
        if success:
            TelegramNotification.objects.filter(pk=pk).update(sent_at=timezone.now(), lease_until=None)
            sent += 1
        else:
            delay = min(3600, 30 * 2 ** min(event.attempts - 1, 7))
            TelegramNotification.objects.filter(pk=pk).update(
                lease_until=None, next_attempt_at=timezone.now() + timedelta(seconds=delay),
            )
    return sent
