"""
iConnect — Celery Tasks

Background tasks for session management and daily summaries.
"""
from celery import shared_task
from django.utils import timezone
from django.db.models import Sum, Count, Avg
from django.db.models.functions import ExtractHour
import logging

logger = logging.getLogger(__name__)


@shared_task
def restore_iptables_on_boot():
    """
    Restore iptables rules after reboot.
    Active sessions: allow internet. Paused sessions: keep blocked.
    """
    from .models import Session
    from . import iptables

    active = Session.objects.filter(status='active')
    paused = Session.objects.filter(status='paused')

    allowed = 0
    for session in active:
        if session.time_remaining_seconds > 0:
            iptables.allow_device(session.mac_address)
            allowed += 1
        else:
            session.expire_session()
            iptables.block_device(session.mac_address)

    blocked = 0
    for session in paused:
        iptables.block_device(session.mac_address)
        blocked += 1

    logger.info(f'Boot: restored {allowed} active, {blocked} paused iptables rules')
    return f'Restored {allowed} active, {blocked} paused'


@shared_task
def check_expired_sessions():
    """
    Check and expire sessions that have run out of time.
    Should be called every minute by Celery Beat.
    """
    from .models import Session
    from . import iptables

    active_sessions = Session.objects.filter(status='active')
    expired_count = 0

    for session in active_sessions:
        if session.time_remaining_seconds <= 0:
            session.expire_session()
            iptables.block_device(session.mac_address)
            expired_count += 1
            logger.info(f'Expired session {session.id} for {session.mac_address}')

    if expired_count:
        logger.info(f'Expired {expired_count} sessions')

    return f'Checked {active_sessions.count()} sessions, expired {expired_count}'


@shared_task
def update_active_session_bandwidth():
    """Refresh estimated bandwidth_used_mb for active sessions."""
    from .models import Session
    from .bandwidth import refresh_session_bandwidth_usage

    active_sessions = Session.objects.filter(status='active').select_related('plan')
    updated = 0

    for session in active_sessions:
        if refresh_session_bandwidth_usage(session):
            updated += 1

    if updated:
        logger.info(f'Updated bandwidth usage for {updated} active sessions')

    return f'Checked {active_sessions.count()} sessions, updated {updated}'


@shared_task
def enforce_pre_auth_dns_policy():
    """Keep DNS-only pre-auth firewall policy in place when enabled."""
    from django.conf import settings
    from . import iptables

    if not getattr(settings, 'PISONET_DNS_ONLY_PREAUTH', False):
        return 'DNS pre-auth policy disabled'

    applied = iptables.apply_pre_auth_dns_policy()
    if applied:
        logger.info('DNS pre-auth policy enforced successfully')
        return 'DNS pre-auth policy enforced'

    logger.warning('DNS pre-auth policy enforcement failed')
    return 'DNS pre-auth policy enforcement failed'


@shared_task
def generate_daily_summary():
    """
    Generate daily revenue summary. Should be called at end of each day.
    """
    from .models import Session
    from dashboard.models import DailyRevenueSummary

    today = timezone.now().date()

    # Get today's data
    sessions_today = Session.objects.filter(
        time_in__date=today,
        status__in=['active', 'expired']
    )

    total_revenue = sessions_today.aggregate(total=Sum('amount_paid'))['total'] or 0
    total_sessions = sessions_today.count()
    avg_minutes = sessions_today.aggregate(avg=Avg('duration_minutes_purchased'))['avg'] or 0

    # Find peak hour
    peak_data = sessions_today.annotate(
        hour=ExtractHour('time_in')
    ).values('hour').annotate(
        count=Count('id')
    ).order_by('-count').first()

    peak_hour = peak_data['hour'] if peak_data else None

    # Create or update summary
    summary, created = DailyRevenueSummary.objects.update_or_create(
        date=today,
        defaults={
            'total_revenue': total_revenue,
            'total_sessions': total_sessions,
            'avg_session_minutes': round(avg_minutes, 1),
            'peak_hour': peak_hour,
        }
    )

    action = 'Created' if created else 'Updated'
    logger.info(f'{action} daily summary for {today}: ₱{total_revenue}, {total_sessions} sessions')

    return f'{action} summary for {today}'


@shared_task
def expire_voucher_codes():
    """
    Expire voucher codes older than 5 minutes that haven't been used.
    """
    from .models import Session
    from django.conf import settings

    expiry_minutes = getattr(settings, 'PISONET_VOUCHER_EXPIRY_MINUTES', 5)
    cutoff = timezone.now() - timezone.timedelta(minutes=expiry_minutes)

    expired = Session.objects.filter(
        status='paused',
        voucher_code__isnull=False,
        created_at__lt=cutoff
    ).update(status='expired')

    if expired:
        logger.info(f'Expired {expired} unused voucher codes')

    return f'Expired {expired} voucher codes'



