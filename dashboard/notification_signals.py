"""Only event changes enqueue alerts; viewing a page never sends notifications."""
import logging
import hashlib
import json
import re
from uuid import uuid4

from django.db import transaction
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.utils import timezone

from dashboard.models import IssueReport, SystemSettings, TelegramNotification
from sessions_app.models import SuspiciousDevice

logger = logging.getLogger(__name__)


def enqueue(kind, key, body, using):
    try:
        # A nested savepoint prevents notification failures breaking the request.
        with transaction.atomic(using=using):
            config = SystemSettings.objects.using(using).filter(pk=1).first()
            flag = 'telegram_notify_tickets' if kind == 'ticket' else 'telegram_notify_security'
            if config and config.enable_telegram_bot and getattr(config, flag):
                TelegramNotification.objects.using(using).get_or_create(
                    event_key=key, defaults={
                        'kind': kind,
                        'body': re.sub(r'\d{6,15}:[A-Za-z0-9_-]{25,60}', '[redacted bot token]', body)[:3500],
                    },
                )
    except Exception as error:
        logger.error('Telegram event enqueue failed (%s)', type(error).__name__)


def local_time(value):
    return timezone.localtime(value).strftime('%b %d, %Y %I:%M %p')


@receiver(pre_save, sender=IssueReport, dispatch_uid='telegram_ticket_before_save')
def ticket_before(sender, instance, raw, using, update_fields, **kwargs):
    if raw or (update_fields is not None and not {'status', 'admin_reply'}.intersection(update_fields)):
        instance._telegram_before = None
        return
    instance._telegram_before = sender.objects.using(using).filter(pk=instance.pk).values('status', 'admin_reply').first() if instance.pk else None


@receiver(post_save, sender=IssueReport, dispatch_uid='telegram_ticket_after_save')
def ticket_after(sender, instance, created, raw, using, update_fields, **kwargs):
    if raw:
        return
    previous = getattr(instance, '_telegram_before', None)
    if previous and update_fields is not None:
        # Alert only about persisted fields, not unrelated unsaved in-memory edits.
        instance = sender.objects.using(using).get(pk=instance.pk)
    changed = previous and (previous['status'] != instance.status or previous['admin_reply'] != instance.admin_reply)
    if not created and not changed:
        return
    title = 'New Support Ticket' if created else 'Support Ticket Updated'
    key = f'ticket:{instance.pk}:created' if created else f'ticket:{instance.pk}:update:{uuid4().hex}'
    body = (
        f'{title} #{instance.pk}\n'
        f'Category: {instance.get_category_display()}\n'
        f'Status: {instance.get_status_display()}\n'
        f'Device: {instance.mac_address or "Unknown MAC"}\n'
        f'Contact: {instance.contact_info or "Not provided"}\n'
        f'Message: {instance.message[:1500]}\n'
    )
    if instance.admin_reply:
        body += f'Reply: {instance.admin_reply[:900]}\n'
    body += f'Time: {local_time(timezone.now())} (Asia/Manila)\nOpen Support Tickets in the admin portal or use /tickets.'
    # Internal operator notes and credentials are intentionally not included.
    enqueue('ticket', key, body, using)


@receiver(pre_save, sender=SuspiciousDevice, dispatch_uid='telegram_security_before_save')
def security_before(sender, instance, raw, using, **kwargs):
    if raw:
        return
    instance._telegram_before = sender.objects.using(using).filter(pk=instance.pk).values(
        'status', 'resolved_at', 'detection_count', 'last_detected_at',
    ).first() if instance.pk else None


@receiver(post_save, sender=SuspiciousDevice, dispatch_uid='telegram_security_after_save')
def security_after(sender, instance, created, raw, using, update_fields, **kwargs):
    if raw or instance.status != SuspiciousDevice.STATUS_NEW:
        return
    if update_fields is not None and 'status' not in update_fields:
        return
    previous = getattr(instance, '_telegram_before', None)
    reopened = previous and previous['status'] != SuspiciousDevice.STATUS_NEW
    if not created and not reopened:
        return
    title = 'New Security Alert' if created else 'Security Alert Reopened'
    # Concurrent reopens from the same resolved state share an event key.
    revision = hashlib.sha256(json.dumps(previous, sort_keys=True, default=str).encode()).hexdigest()[:32]
    key = f'security:{instance.pk}:created' if created else f'security:{instance.pk}:reopen:{revision}'
    body = (
        f'{title} #{instance.pk}\n'
        f'Device: {instance.mac_address}\nIP: {instance.last_ip_address or "Unknown"}\n'
        f'Reason: {instance.reason}\nEvidence: {instance.evidence[:1000] or "Not provided"}\n'
        f'Detections: {instance.detection_count}\n'
        f'Time: {local_time(instance.last_detected_at)} (Asia/Manila)\n'
        'Open Security in the admin portal to review. This is a detection, not proof of an attack.'
    )
    enqueue('security', key, body, using)
