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
    - Active sessions: AUTO-PAUSE them (Pi was off, so timer should not have been running).
      The downtime is added to total_paused_seconds so no time is lost.
    - Already-paused sessions: keep blocked.
    """
    from .models import Session
    from . import iptables

    active = Session.objects.filter(status='active')
    paused = Session.objects.filter(status='paused')

    reallowed = 0
    expired = 0
    for session in active:
        if session.time_remaining_seconds > 0:
            # Restore internet access for the active session
            dl_kbps = int(session.plan.speed_limit * 1024) if session.plan and session.plan.speed_limit else None
            ul_kbps = int(session.plan.speed_limit_upload * 1024) if session.plan and session.plan.speed_limit_upload else dl_kbps
            iptables.allow_device(session.mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps)
            reallowed += 1
            logger.info(f'Boot: restored active session {session.id} for {session.mac_address}')
        else:
            session.expire_session()
            iptables.block_device(session.mac_address)
            expired += 1

    blocked = 0
    for session in paused:
        iptables.block_device(session.mac_address)
        blocked += 1

    logger.info(f'Boot: restored {reallowed}, expired {expired}, kept-paused {blocked}')
    return f'Restored {reallowed}, expired {expired}, kept-paused {blocked}'


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
            logger.info(f'Expired active session {session.id} for {session.mac_address}')

    from django.conf import settings
    from datetime import timedelta
    max_pause_hours = getattr(settings, 'PISONET_MAX_PAUSE_HOURS', 24)
    if max_pause_hours > 0:
        pause_cutoff = timezone.now() - timedelta(hours=max_pause_hours)
        paused_sessions = Session.objects.filter(status='paused', paused_at__lt=pause_cutoff)
        for session in paused_sessions:
            session.expire_session()
            iptables.block_device(session.mac_address)
            expired_count += 1
            logger.info(f'Expired paused session {session.id} for {session.mac_address} (exceeded {max_pause_hours}h pause limit)')

    if expired_count:
        logger.info(f'Expired {expired_count} total sessions')

    return f'Checked {active_sessions.count()} active sessions, expired {expired_count} total sessions'


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
def auto_pause_disconnected_sessions():
    """
    Auto-pause sessions whose devices have disconnected from WiFi.

    Pings each active session's IP address. If a device is unreachable for
    longer than AUTO_PAUSE_TIMEOUT_SECONDS (default 120s / 2 minutes),
    the session is automatically paused to save the user's remaining time.

    Uses Django cache to track when each device was first seen as unreachable.
    """
    from django.conf import settings
    from django.core.cache import cache
    from .models import Session
    from . import iptables
    import subprocess

    if not getattr(settings, 'PISONET_AUTO_PAUSE_ENABLED', True):
        return 'Auto-pause disabled'

    if getattr(settings, 'PISONET_GPIO_SIMULATION', False):
        return 'Skipped in simulation mode'

    timeout_seconds = getattr(settings, 'PISONET_AUTO_PAUSE_TIMEOUT_SECONDS', 120)
    active_sessions = Session.objects.filter(status='active').exclude(ip_address__isnull=True)

    paused_count = 0
    cleared_count = 0

    for session in active_sessions:
        ip = session.ip_address
        cache_key = f'auto_pause_unreachable_{session.mac_address}'

        # Ping the device: 1 ping, 1 second timeout
        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '1', ip],
                capture_output=True, timeout=3,
            )
            is_reachable = result.returncode == 0
        except Exception:
            is_reachable = False

        if is_reachable:
            # Device is online — clear any unreachable tracking
            if cache.get(cache_key):
                cache.delete(cache_key)
                cleared_count += 1
        else:
            # Device is unreachable — track how long
            first_unreachable = cache.get(cache_key)
            now = timezone.now()

            if first_unreachable is None:
                # First time we noticed this device is gone
                cache.set(cache_key, now.isoformat(), timeout=timeout_seconds + 120)
            else:
                # Check if unreachable long enough to auto-pause
                from datetime import datetime, timezone as dt_tz
                if isinstance(first_unreachable, str):
                    first_unreachable = datetime.fromisoformat(first_unreachable)
                elapsed = (now - first_unreachable).total_seconds()

                if elapsed >= timeout_seconds:
                    session.pause_session()
                    iptables.block_device(session.mac_address)
                    cache.delete(cache_key)
                    paused_count += 1
                    logger.info(
                        f'Auto-paused session {session.id} for {session.mac_address} '
                        f'(unreachable for {int(elapsed)}s at IP {ip})'
                    )

    if paused_count or cleared_count:
        logger.info(f'Auto-pause check: paused={paused_count}, reconnected={cleared_count}')

    return f'Checked {active_sessions.count()} sessions, auto-paused {paused_count}, reconnected {cleared_count}'


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



