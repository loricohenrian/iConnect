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
    import os
    from django.conf import settings

    # Record heartbeat so we can calculate downtime on reboot
    heartbeat_path = os.path.join(settings.BASE_DIR, 'heartbeat.txt')
    try:
        with open(heartbeat_path, 'w') as f:
            f.write(str(timezone.now().timestamp()))
    except Exception as e:
        logger.error(f'Failed to write heartbeat: {e}')

    active_sessions = Session.objects.filter(status='active')
    expired_count = 0

    for session in active_sessions:
        if session.time_remaining_seconds <= 0:
            session.expire_session()
            iptables.block_device(session.mac_address)
            expired_count += 1
            logger.info(f'Expired active session {session.id} for {session.mac_address}')

    from datetime import timedelta
    from dashboard.models import SystemSettings
    global_max_pause_hours = SystemSettings.get_settings().global_pause_limit_hours
    paused_sessions = Session.objects.filter(status='paused').select_related('plan')
    
    resumed_count = 0
    for session in paused_sessions:
        # Use plan-specific limit if set, otherwise global
        max_pause_hours = session.plan.pause_duration_limit if session.plan and session.plan.pause_duration_limit > 0 else global_max_pause_hours
        
        if max_pause_hours > 0 and session.paused_at:
            paused_hours = (timezone.now() - session.paused_at).total_seconds() / 3600.0
            if paused_hours >= max_pause_hours:
                # Limit exceeded: auto-resume the session so the timer drains
                session.resume_session()
                # Do NOT allow iptables here since they might not be connected, 
                # but if they are, they will need to refresh anyway. 
                # Better yet, allow them just in case.
                dl_kbps = int(session.plan.speed_limit * 1024) if session.plan and session.plan.speed_limit else None
                ul_kbps = int(session.plan.speed_limit_upload * 1024) if session.plan and session.plan.speed_limit_upload else dl_kbps
                iptables.allow_device(session.mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps)
                resumed_count += 1
                logger.info(f'Auto-resumed paused session {session.id} for {session.mac_address} (exceeded {max_pause_hours}h pause limit)')

    if expired_count or resumed_count:
        logger.info(f'Expired {expired_count} total sessions. Auto-resumed {resumed_count} sessions.')

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
    from dashboard.models import SystemSettings
    from .models import Session
    from . import iptables

    settings_obj = SystemSettings.get_settings()
    if not settings_obj.enable_auto_pause_resume:
        return 'Auto-pause/resume disabled'

    paused_sessions = Session.objects.filter(status='paused').exclude(ip_address__isnull=True)
    resumed_count = 0

    for session in paused_sessions:
        if _is_device_reachable(session.mac_address, session.ip_address):
            # Device is back online — check if max pause duration was exceeded
            max_pause_hours = session.plan.pause_duration_limit if (session.plan and session.plan.pause_duration_limit > 0) else settings_obj.global_pause_limit_hours
            if max_pause_hours > 0 and session.paused_at:
                paused_hours = (timezone.now() - session.paused_at).total_seconds() / 3600.0
                if paused_hours >= max_pause_hours:
                    session.expire_session()
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
            dl_kbps = int(session.plan.speed_limit * 1024) if session.plan and session.plan.speed_limit else None
            ul_kbps = int(session.plan.speed_limit_upload * 1024) if session.plan and session.plan.speed_limit_upload else dl_kbps
            iptables.allow_device(session.mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps)
            resumed_count += 1
            logger.info(f'Auto-resumed session {session.id} for {session.mac_address}')

    if resumed_count:
        logger.info(f'Auto-resume check: resumed={resumed_count}')

    return f'Checked {paused_sessions.count()} paused sessions, auto-resumed {resumed_count}'


def _safe_cache_get(key, default=None):
    try:
        from django.core.cache import cache
        return cache.get(key, default)
    except Exception:
        return default


def _safe_cache_set(key, val, timeout=None):
    try:
        from django.core.cache import cache
        cache.set(key, val, timeout=timeout)
    except Exception:
        pass


def _safe_cache_delete(key):
    try:
        from django.core.cache import cache
        cache.delete(key)
    except Exception:
        pass


@shared_task
def check_internet_status():
    """
    Check if the ISP is online using fast TCP probe (8.8.8.8:53 / 1.1.1.1:53) + ping fallback.
    Caches the result so the API endpoints and portal can quickly check.
    """
    import socket
    import subprocess
    from dashboard.models import SystemSettings

    settings_obj = SystemSettings.get_settings()
    if not settings_obj.enable_internet_check:
        # If disabled, always assume internet is OK
        _safe_cache_set("internet_status_ok", True, timeout=120)
        return 'Internet checking disabled'

    is_online = False
    # Fast TCP probe to DNS port 53 (reliable and non-blocking)
    for host in ("8.8.8.8", "1.1.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.5)
            s.connect((host, 53))
            s.close()
            is_online = True
            break
        except Exception:
            continue

    if not is_online:
        try:
            # ICMP ping fallback
            res = subprocess.run(
                ["ping", "-c", "1", "-W", "2", "8.8.8.8"],
                capture_output=True, timeout=3,
            )
            is_online = (res.returncode == 0)
        except Exception:
            is_online = False

    _safe_cache_set("internet_status_ok", is_online, timeout=120)
    
    from dashboard.models import Announcement
    from sessions_app.models import Session

    if not is_online:
        fail_count = (_safe_cache_get("isp_fail_count") or 0) + 1
        _safe_cache_set("isp_fail_count", fail_count, timeout=300)
        logger.warning(f"Internet check probe failed ({fail_count}/2). ISP may be offline.")

        # Require 2 consecutive failed probes (~45-60s) to avoid momentary glitches
        if fail_count >= 2 and not _safe_cache_get("isp_outage_active"):
            _safe_cache_set("isp_outage_active", True, timeout=None)
            logger.error("ISP Outage confirmed! Freezing active student sessions to protect time...")

            # 1. Freeze all active sessions
            active_sessions = list(Session.objects.filter(status="active"))
            paused_ids = []
            for s in active_sessions:
                try:
                    s.pause_session()
                    try:
                        from sessions_app import iptables
                        iptables.block_device(s.mac_address)
                    except Exception:
                        pass
                    paused_ids.append(s.id)
                except Exception as e:
                    logger.error(f"Failed to pause session {s.id} during outage: {e}")

            _safe_cache_set("isp_paused_session_ids", paused_ids, timeout=None)

            # 2. Automatically post announcement on captive portal
            ann_text = (
                "⚠️ NOTICE: Internet is temporarily interrupted by our ISP. "
                "All user timers have been FROZEN to protect your remaining time! "
                "Your timer will automatically resume as soon as connection is restored."
            )
            ann = Announcement.objects.create(message=ann_text, is_active=True)
            _safe_cache_set("isp_outage_announcement_id", ann.id, timeout=None)

            # 3. Send Telegram Outage Alert
            try:
                from dashboard.telegram_bot import send_telegram_message, get_telegram_config
                cfg = get_telegram_config()
                if cfg.get("enabled") and cfg.get("notify_isp_down"):
                    send_telegram_message(
                        f"🚨 *ISP OUTAGE DETECTED!*\n"
                        f"Upstream internet connection dropped.\n\n"
                        f"⏸ *Action Taken:* Automatically paused `{len(paused_ids)}` active student session(s) to protect customer time.\n"
                        f"📢 Outage notice displayed on captive portal screen."
                    )
            except Exception as tg_err:
                logger.warning(f"Failed to send Telegram outage alert: {tg_err}")

            return f"ISP outage handled: paused {len(paused_ids)} sessions"

        return f"ISP probe failed ({fail_count}/2)"

    else:
        # ISP is online
        _safe_cache_delete("isp_fail_count")

        # Check if recovering from a previous outage
        if _safe_cache_get("isp_outage_active"):
            logger.info("ISP internet connection restored! Resuming student sessions...")
            _safe_cache_delete("isp_outage_active")

            # 1. Resume the sessions that were paused by this outage
            paused_ids = _safe_cache_get("isp_paused_session_ids") or []
            resumed_count = 0
            if paused_ids:
                sessions_to_resume = Session.objects.filter(id__in=paused_ids, status="paused")
                for s in sessions_to_resume:
                    try:
                        s.resume_session()
                        try:
                            from sessions_app import iptables
                            iptables.allow_device(s.mac_address)
                        except Exception:
                            pass
                        resumed_count += 1
                    except Exception as e:
                        logger.error(f"Failed to resume session {s.id}: {e}")
                _safe_cache_delete("isp_paused_session_ids")

            # 2. Remove outage announcement
            ann_id = _safe_cache_get("isp_outage_announcement_id")
            if ann_id:
                try:
                    Announcement.objects.filter(id=ann_id).update(is_active=False)
                except Exception:
                    pass
                _safe_cache_delete("isp_outage_announcement_id")

            # 3. Send Telegram Recovery Alert
            try:
                from dashboard.telegram_bot import send_telegram_message, get_telegram_config
                cfg = get_telegram_config()
                if cfg.get("enabled") and cfg.get("notify_isp_down"):
                    send_telegram_message(
                        f"🟢 *ISP INTERNET RESTORED!*\n"
                        f"Upstream connection is back online.\n\n"
                        f"▶️ *Action Taken:* Automatically resumed `{resumed_count}` student session(s).\n"
                        f"🧹 Removed outage announcement from captive portal screen."
                    )
            except Exception as tg_err:
                logger.warning(f"Failed to send Telegram recovery alert: {tg_err}")

            return f"ISP restored: resumed {resumed_count} sessions"

        return "ISP is online"


