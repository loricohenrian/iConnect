"""
iConnect — Celery Tasks

Background tasks for session management and daily summaries.
"""
from celery import shared_task
from django.utils import timezone
from django.db.models import Sum, Count, Avg, Q
from django.db.models.functions import ExtractHour
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from uuid import uuid4

logger = logging.getLogger(__name__)


@shared_task
def restore_iptables_on_boot():
    """Recover power-loss balances before restoring firewall rules."""
    from .models import Session
    from . import iptables
    from .power_recovery import ensure_power_recovery
    ensure_power_recovery()

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
    from .power_recovery import ensure_power_recovery

    ensure_power_recovery()

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
        if session.power_paused:
            # Power-loss holds do not spend user pause allowances or expire overnight.
            continue
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
            if ((now - session.time_in).total_seconds() - session.power_credited_seconds) / 3600.0 >= max_lifetime_hours:
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


@shared_task
def check_expired_sessions():
    """
    Check and expire sessions that have run out of time or exceeded pause limits.
    Should be called regularly by Celery Beat.
    """
    from .power_recovery import checkpoint_sessions
    checkpoint_sessions()

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


def _is_device_reachable(mac_address, ip_address, fresh=False):
    """Require a fresh ARP reply from the session MAC, never a cached entry.

    ``fresh`` remains accepted for existing power-recovery callers. All checks
    are now fresh. None means the probe itself failed, not that WiFi is offline.
    """
    if not ip_address or not mac_address:
        return False
    mac_upper = (mac_address or "").upper().strip()
    try:
        res = subprocess.run(
            ["arping", "-c", "1", "-w", "1", ip_address],
            capture_output=True, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning('WiFi reachability probe unavailable for %s', ip_address)
        return None
    if res.returncode not in (0, 1):
        logger.warning('WiFi reachability probe failed for %s (exit %s)', ip_address, res.returncode)
        return None
    output = res.stdout or b''
    if isinstance(output, bytes):
        output = output.decode(errors='replace')
    return res.returncode == 0 and f'[{mac_upper}]' in output.upper()


def _probe_session_devices(sessions):
    """Run at most eight bounded network probes at once; no ORM in threads."""
    targets = {(s.mac_address, s.ip_address) for s in sessions}
    if not targets:
        return {}
    targets = sorted(targets)
    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as executor:
        results = list(executor.map(lambda target: _is_device_reachable(*target, fresh=True), targets))
    by_device = dict(zip(targets, results))
    return {s.pk: by_device[(s.mac_address, s.ip_address)] for s in sessions}


def _single_flight_scan(name):
    """Do not let periodic copies of the same scan monopolize the worker."""
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            from django.core.cache import cache
            key = f'wifi_scan_lock:{name}'
            token = uuid4().hex
            if not cache.add(key, token, timeout=120):
                return f'Skipped {name}: scan already running'
            try:
                return function(*args, **kwargs)
            finally:
                if cache.get(key) == token:
                    cache.delete(key)
        return run
    return decorate


def _disconnect_cache_key(session):
    # A new paid session must not inherit an older session's offline timer.
    return f'auto_pause_unreachable_session:{session.pk}'


@shared_task
@_single_flight_scan('auto-pause')
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
    from django.db import transaction
    from .models import Session
    from . import iptables

    from dashboard.models import SystemSettings
    from .power_recovery import ensure_power_recovery
    ensure_power_recovery()
    
    settings_obj = SystemSettings.get_settings()
    if not settings_obj.enable_auto_pause_resume:
        return 'Auto-pause/resume disabled'

    if getattr(settings, 'PISONET_GPIO_SIMULATION', False):
        return 'Skipped in simulation mode'

    timeout_seconds = settings_obj.auto_pause_timeout_seconds
    active_sessions = list(Session.objects.filter(status='active').exclude(
        ip_address__isnull=True).select_related('plan'))
    reachability = _probe_session_devices(active_sessions)

    paused_count = 0
    cleared_count = 0

    for session in active_sessions:
        ip = session.ip_address
        cache_key = _disconnect_cache_key(session)

        is_reachable = reachability[session.pk]
        if is_reachable is None:
            # Missing tools/permissions are not evidence of a disconnected phone.
            continue

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
                    try:
                        with transaction.atomic():
                            current = Session.objects.select_for_update(of=('self',)).select_related('plan').filter(
                                pk=session.pk, status='active', ip_address=ip,
                                mac_address=session.mac_address).first()
                            if current is None:
                                cache.delete(cache_key)
                                continue
                            limit = current.effective_pause_limit
                            if limit > 0 and current.pause_count >= limit:
                                continue
                            if not current.pause_session():
                                continue
                            # Do not freeze billing while failed revocation leaves
                            # internet access open. Roll back and retry next scan.
                            if not iptables.block_device(current.mac_address):
                                raise RuntimeError('Could not block auto-paused device')
                        cache.delete(cache_key)
                        cache.set(f"auto_paused_{session.id}", True, timeout=86400 * 7)
                        cache.delete(f"manual_pause_{session.id}")
                    except Exception:
                        logger.exception('Could not auto-pause disconnected session %s', session.pk)
                        continue
                    paused_count += 1
                    logger.info(
                        f'Auto-paused session {session.id} for {session.mac_address} '
                        f'(unreachable for {int(elapsed)}s at IP {ip})'
                    )

    if paused_count or cleared_count:
        logger.info(f'Auto-pause check: paused={paused_count}, reconnected={cleared_count}')

    return f'Checked {len(active_sessions)} sessions, auto-paused {paused_count}, reconnected {cleared_count}'


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
    from .power_recovery import ensure_power_recovery
    ensure_power_recovery()

    expiry_minutes = getattr(settings, 'PISONET_VOUCHER_EXPIRY_MINUTES', 5)
    cutoff = timezone.now() - timezone.timedelta(minutes=expiry_minutes)

    expired = Session.objects.filter(
        status='paused',
        power_paused=False,
        # Unused voucher sessions have never entered a real pause. Disconnect,
        # manual and ISP pauses all set paused_at and must retain their balance.
        paused_at__isnull=True,
        pause_count=0,
        voucher_code__isnull=False,
        created_at__lt=cutoff
    ).update(status='expired')

    if expired:
        logger.info(f'Expired {expired} unused voucher codes')

    return f'Expired {expired} voucher codes'

@shared_task
@_single_flight_scan('auto-resume')
def auto_resume_connected_sessions():
    """
    Auto-resume paused sessions whose devices have reconnected to WiFi.
    """
    from django.core.cache import cache
    from dashboard.models import SystemSettings
    from .models import Session
    from . import iptables
    from .capacity import session_capacity_lock
    from .power_recovery import ensure_power_recovery
    ensure_power_recovery()

    settings_obj = SystemSettings.get_settings()
    if not settings_obj.enable_auto_pause_resume and not Session.objects.filter(status='paused', power_paused=True).exists():
        return 'Auto-pause/resume disabled'

    # Do NOT auto-resume any sessions if an ISP outage is active
    from sessions_app.internet_monitor import check_isp_internet_status
    isp_info = check_isp_internet_status(force_probe=False)
    if isp_info.get("isp_outage"):
        return 'Auto-resume skipped: ISP outage is active'

    paused_sessions = list(Session.objects.filter(status='paused').exclude(
        ip_address__isnull=True).select_related('plan'))
    eligible_sessions = []
    resumed_count = 0

    for session in paused_sessions:
        if session.power_paused:
            from .models import SuspiciousDevice
            if SuspiciousDevice.objects.filter(Q(status='blocked') | Q(is_blocked=True),
                                               mac_address__iexact=session.mac_address).exists():
                continue
        if not session.power_paused and not settings_obj.enable_auto_pause_resume:
            continue
        # Never auto-resume a session that was manually paused or outage-paused
        if not session.power_paused and (cache.get(f"manual_pause_{session.id}") or cache.get(f"outage_paused_{session.id}")):
            continue

        # Only auto-resume sessions that were paused automatically due to WiFi disconnection
        if not session.power_paused and not cache.get(f"auto_paused_{session.id}"):
            continue

        eligible_sessions.append(session)

    reachability = _probe_session_devices(eligible_sessions)
    for session in eligible_sessions:
        if not reachability[session.pk]:
            continue
        granted = False
        try:
            # Probe outside the lock, but validate and reserve the slot inside
            # the same lock used by web admissions and administrator resumes.
            with session_capacity_lock() as current_settings:
                current = Session.objects.select_for_update(of=('self',)).select_related('plan').filter(
                    pk=session.pk, status='paused', ip_address=session.ip_address,
                    mac_address=session.mac_address).first()
                if current is None or current.power_paused != session.power_paused:
                    continue
                if not current.power_paused:
                    if not current_settings.enable_auto_pause_resume:
                        continue
                    if (cache.get(f'manual_pause_{current.id}') or
                            cache.get(f'outage_paused_{current.id}') or
                            not cache.get(f'auto_paused_{current.id}')):
                        continue
                    max_pause_hours = current.plan.pause_duration_limit if (
                        current.plan and current.plan.pause_duration_limit > 0
                    ) else current_settings.global_pause_limit_hours
                    if max_pause_hours > 0 and current.paused_at:
                        paused_hours = (timezone.now() - current.paused_at).total_seconds() / 3600.0
                        if paused_hours >= max_pause_hours:
                            current.expire_session()
                            cache.delete(f'auto_paused_{current.id}')
                            cache.delete(f'manual_pause_{current.id}')
                            continue
                from .models import SuspiciousDevice
                if SuspiciousDevice.objects.filter(Q(status='blocked') | Q(is_blocked=True),
                                                   mac_address__iexact=current.mac_address).exists():
                    continue
                if current.time_remaining_seconds <= 0:
                    continue
                if Session.objects.filter(status='active').count() >= current_settings.max_concurrent_sessions:
                    continue
                if current.power_paused:
                    from .power_recovery import resume_power_session
                    if not resume_power_session(current):
                        continue
                else:
                    dl_kbps = int(current.plan.speed_limit * 1024) if current.plan and current.plan.speed_limit else None
                    ul_kbps = int(current.plan.speed_limit_upload * 1024) if current.plan and current.plan.speed_limit_upload else dl_kbps
                    if not iptables.allow_device(current.mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps):
                        iptables.block_device(current.mac_address)
                        continue
                    granted = True
                    if not current.resume_session():
                        iptables.block_device(current.mac_address)
                        continue
            cache.delete(f"auto_paused_{session.id}")
            cache.delete(f"manual_pause_{session.id}")
            cache.delete(f"outage_paused_{session.id}")
            resumed_count += 1
            logger.info(f'Auto-resumed session {session.id} for {session.mac_address}')
        except Exception:
            logger.exception('Could not auto-resume disconnected session %s', session.pk)
            if granted:
                # Only revoke a rolled-back grant, not a competing resume.
                with session_capacity_lock():
                    current = Session.objects.select_for_update().filter(pk=session.pk, status='paused').first()
                    if current:
                        iptables.block_device(current.mac_address)

    if resumed_count:
        logger.info(f'Auto-resume check: resumed={resumed_count}')

    return f'Checked {len(paused_sessions)} paused sessions, auto-resumed {resumed_count}'


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
        return f"ISP restored: {status.get('resumed_count', 0)} sessions auto-resumed"
    if status.get("isp_outage"):
        return f"ISP outage active: {status.get('message', '')}"
    if not status.get("is_online"):
        return "ISP probe failed (pending debounce)"
    return "ISP is online"


