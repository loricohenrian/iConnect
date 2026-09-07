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
    import os
    from django.conf import settings

    # Calculate downtime from heartbeat
    downtime_seconds = 0
    heartbeat_path = os.path.join(settings.BASE_DIR, 'heartbeat.txt')
    try:
        if os.path.exists(heartbeat_path):
            with open(heartbeat_path, 'r') as f:
                last_heartbeat = float(f.read().strip())
                downtime_seconds = timezone.now().timestamp() - last_heartbeat
                
                # Minimum 60s downtime to matter, max 30 days cap
                if downtime_seconds < 60:
                    downtime_seconds = 0
                elif downtime_seconds > 2592000:
                    downtime_seconds = 2592000
                    
            # Clear heartbeat to prevent double-counting
            os.remove(heartbeat_path)
    except Exception as e:
        logger.error(f'Failed to process heartbeat on boot: {e}')

    active = Session.objects.filter(status='active')
    paused = Session.objects.filter(status='paused')

    reallowed = 0
    expired = 0
    
    if downtime_seconds > 0:
        logger.info(f'Boot: Refunding {int(downtime_seconds)}s of downtime to {active.count()} active sessions.')

    for session in active:
        # Refund downtime BEFORE checking remaining time
        if downtime_seconds > 0:
            session.total_paused_seconds += downtime_seconds
            session.save(update_fields=['total_paused_seconds'])

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

    # Expire any paused sessions that exceeded their limit while the system was off
    stale_expired = cleanup_expired_and_stale_sessions()

    logger.info(f'Boot: restored {reallowed}, expired {expired + stale_expired}, kept-paused {blocked}')
    return f'Restored {reallowed}, expired {expired + stale_expired}, kept-paused {blocked}'


@shared_task
def cleanup_expired_and_stale_sessions():
    """
    Check active and paused sessions and expire any that ran out of time
    or exceeded their pause duration limit. Returns count of expired sessions.
    """
    from .models import Session
    from . import iptables
    from dashboard.models import SystemSettings

    now = timezone.now()
    settings_obj = SystemSettings.get_settings()
    global_max_pause = settings_obj.global_pause_limit_hours if settings_obj else 24
    expired_count = 0

    # 1. Active sessions with no time remaining
    for session in Session.objects.filter(status='active').select_related('plan'):
        if session.time_remaining_seconds <= 0:
            session.expire_session()
            try:
                iptables.block_device(session.mac_address)
            except Exception:
                pass
            expired_count += 1
            logger.info(f'Expired active session {session.id} for {session.mac_address} (time expired)')

    # 2. Paused sessions exceeding pause limit or with no time remaining
    for session in Session.objects.filter(status='paused').select_related('plan'):
        max_pause_hours = session.plan.pause_duration_limit if (session.plan and session.plan.pause_duration_limit > 0) else global_max_pause
        if max_pause_hours == 0 and session.amount_paid:
            from .models import Plan
            matching_plan = Plan.objects.filter(is_active=True, price=session.amount_paid, pause_duration_limit__gt=0).first()
            if matching_plan:
                max_pause_hours = matching_plan.pause_duration_limit
        if max_pause_hours == 0:
            max_pause_hours = 48  # Default 48h safety ceiling for paused sessions

        should_expire = False

        if max_pause_hours > 0 and session.paused_at:
            paused_hours = (now - session.paused_at).total_seconds() / 3600.0
            if paused_hours >= max_pause_hours:
                should_expire = True
                logger.info(f'Expiring paused session {session.id} for {session.mac_address} (exceeded {max_pause_hours}h pause limit)')

        # Also check if total elapsed wall-clock time since session started exceeds (purchased duration + max pause limit)
        if not should_expire and session.time_in and max_pause_hours > 0:
            max_lifetime_hours = (session.duration_minutes_purchased or 0) / 60.0 + max_pause_hours
            if (now - session.time_in).total_seconds() / 3600.0 >= max_lifetime_hours:
                should_expire = True
                logger.info(f'Expiring paused session {session.id} for {session.mac_address} (lifetime exceeded {max_lifetime_hours}h)')

        if not should_expire and session.time_remaining_seconds <= 0:
            should_expire = True
            logger.info(f'Expiring paused session {session.id} for {session.mac_address} (0 time remaining)')

        if should_expire:
            session.expire_session()
            try:
                iptables.block_device(session.mac_address)
            except Exception:
                pass
            expired_count += 1

    return expired_count


def check_expired_sessions():
    """
    Check and expire sessions that have run out of time or exceeded pause limits.
    Should be called regularly by Celery Beat.
    """
    import os
    from django.conf import settings

    # Record heartbeat so we can calculate downtime on reboot
    heartbeat_path = os.path.join(settings.BASE_DIR, 'heartbeat.txt')
    try:
        with open(heartbeat_path, 'w') as f:
            f.write(str(timezone.now().timestamp()))
    except Exception as e:
        logger.error(f'Failed to write heartbeat: {e}')

    expired_count = cleanup_expired_and_stale_sessions()
    if expired_count:
        logger.info(f'Cleaned up and expired {expired_count} sessions.')

    return f'Expired {expired_count} total sessions'


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


def _is_device_reachable(mac_address, ip_address):
    """
    Check if a device is reachable on the local LAN/WiFi.

    Windows Firewall, iPhones, and Android devices often block ICMP pings (ping -c 1).
    Therefore we check Layer 2 ARP table first (/proc/net/arp and arping),
    falling back to ICMP ping only if needed.
    """
    if not ip_address:
        return False

    mac_upper = (mac_address or "").upper().strip()

    # Method 1: Check Linux ARP table (/proc/net/arp)
    try:
        with open("/proc/net/arp", "r") as f:
            for line in f:
                parts = line.split()
                # Format: IP HW_type Flags HW_address Mask Device
                # Flags 0x2 = COMPLETED (0x0 = INCOMPLETE / EXPIRED)
                if len(parts) >= 4:
                    line_ip = parts[0]
                    flags = parts[2]
                    line_mac = parts[3].upper()
                    if (line_ip == ip_address or line_mac == mac_upper) and flags != "0x0":
                        return True
    except (OSError, IOError):
        pass

    # Method 2: Try arping (Layer 2 ARP ping — works even when OS firewall blocks ICMP)
    try:
        res = subprocess.run(
            ["arping", "-c", "1", "-w", "1", ip_address],
            capture_output=True, timeout=2,
        )
        if res.returncode == 0:
            return True
    except Exception:
        pass

    # Method 3: ICMP ping fallback
    try:
        res = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip_address],
            capture_output=True, timeout=2,
        )
        if res.returncode == 0:
            return True
    except Exception:
        pass

    return False


@shared_task
def auto_pause_disconnected_sessions():
    """
    Auto-pause sessions whose devices have disconnected from WiFi.

    Checks device reachability on the local network. If a device is unreachable
    longer than AUTO_PAUSE_TIMEOUT_SECONDS (default 300s / 5 minutes),
    the session is automatically paused to save the user's remaining time.

    Uses Django cache to track when each device was first seen as unreachable.
    """
    from django.conf import settings
    from django.core.cache import cache
    from .models import Session
    from . import iptables

    from dashboard.models import SystemSettings
    
    settings_obj = SystemSettings.get_settings()
    if not settings_obj.enable_auto_pause_resume:
        return 'Auto-pause/resume disabled'

    if getattr(settings, 'PISONET_GPIO_SIMULATION', False):
        return 'Skipped in simulation mode'

    timeout_seconds = settings_obj.auto_pause_timeout_seconds
    active_sessions = Session.objects.filter(status='active').exclude(ip_address__isnull=True)

    paused_count = 0
    cleared_count = 0

    for session in active_sessions:
        ip = session.ip_address
        cache_key = f'auto_pause_unreachable_{session.mac_address}'

        is_reachable = _is_device_reachable(session.mac_address, ip)

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
                    # Respect plan pause limit: if user has exhausted pauses, do not auto-pause
                    if session.plan and session.plan.pause_limit > 0 and session.pause_count >= session.plan.pause_limit:
                        logger.info(
                            f'Skipping auto-pause for session {session.id} ({session.mac_address}): '
                            f'pause limit ({session.plan.pause_limit}) reached.'
                        )
                        continue

                    session.pause_session()
                    iptables.block_device(session.mac_address)
                    cache.delete(cache_key)
                    cache.set(f"auto_paused_{session.id}", True, timeout=86400 * 7)
                    cache.delete(f"manual_pause_{session.id}")
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

@shared_task
def auto_resume_connected_sessions():
    """
    Auto-resume paused sessions whose devices have reconnected to WiFi.
    """
    from django.core.cache import cache
    from dashboard.models import SystemSettings
    from .models import Session
    from . import iptables

    settings_obj = SystemSettings.get_settings()
    if not settings_obj.enable_auto_pause_resume:
        return 'Auto-pause/resume disabled'

    paused_sessions = Session.objects.filter(status='paused').exclude(ip_address__isnull=True)
    resumed_count = 0

    for session in paused_sessions:
        # Never auto-resume a session that was manually paused by the user
        if cache.get(f"manual_pause_{session.id}"):
            continue

        # Only auto-resume sessions that were paused automatically due to WiFi disconnection
        if not cache.get(f"auto_paused_{session.id}"):
            continue

        if _is_device_reachable(session.mac_address, session.ip_address):
            # Device is back online — check if max pause duration was exceeded
            max_pause_hours = session.plan.pause_duration_limit if (session.plan and session.plan.pause_duration_limit > 0) else settings_obj.global_pause_limit_hours
            if max_pause_hours > 0 and session.paused_at:
                paused_hours = (timezone.now() - session.paused_at).total_seconds() / 3600.0
                if paused_hours >= max_pause_hours:
                    session.expire_session()
                    cache.delete(f"auto_paused_{session.id}")
                    cache.delete(f"manual_pause_{session.id}")
                    logger.info(f'Expired session {session.id} for {session.mac_address} during auto-resume: exceeded {max_pause_hours}h pause limit')
                    continue

            # Check if network is full before allowing resume
            from django.conf import settings
            max_sessions = getattr(settings, "PISONET_MAX_CONCURRENT_SESSIONS", 20)
            active_count = Session.objects.filter(status="active").count()
            if active_count >= max_sessions:
                logger.warning(f'Cannot auto-resume {session.mac_address} — network full')
                continue

            session.resume_session()
            cache.delete(f"auto_paused_{session.id}")
            cache.delete(f"manual_pause_{session.id}")
            dl_kbps = int(session.plan.speed_limit * 1024) if session.plan and session.plan.speed_limit else None
            ul_kbps = int(session.plan.speed_limit_upload * 1024) if session.plan and session.plan.speed_limit_upload else dl_kbps
            iptables.allow_device(session.mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps)
            resumed_count += 1
            logger.info(f'Auto-resumed session {session.id} for {session.mac_address}')

    if resumed_count:
        logger.info(f'Auto-resume check: resumed={resumed_count}')

    return f'Checked {paused_sessions.count()} paused sessions, auto-resumed {resumed_count}'


_MEMORY_STATE = {}


def _safe_cache_get(key, default=None):
    try:
        from django.core.cache import cache
        val = cache.get(key)
        if val is not None:
            return val
    except Exception:
        pass
    return _MEMORY_STATE.get(key, default)


def _safe_cache_set(key, val, timeout=None):
    _MEMORY_STATE[key] = val
    try:
        from django.core.cache import cache
        cache.set(key, val, timeout=timeout)
    except Exception:
        pass


def _safe_cache_delete(key):
    _MEMORY_STATE.pop(key, None)
    try:
        from django.core.cache import cache
        cache.delete(key)
    except Exception:
        pass


@shared_task
def check_internet_status():
    """
    Check if the ISP is online using fast TCP probe (8.8.8.8:53 / 1.1.1.1:53) + ping fallback.
    Delegates to the unified internet_monitor engine.
    """
    from sessions_app.internet_monitor import check_isp_internet_status
    status = check_isp_internet_status(force_probe=True)
    if status.get("recovered"):
        return f"ISP restored: resumed {status.get('resumed_count', 0)} sessions"
    if status.get("isp_outage"):
        return f"ISP outage active: {status.get('message', '')}"
    if not status.get("is_online"):
        return "ISP probe failed"
    return "ISP is online"


