import logging
import platform
import socket
import subprocess
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_KEY_STATUS = "isp_internet_status_v2"
CACHE_KEY_FAIL_COUNT = "isp_fail_count"
CACHE_KEY_PAUSED_IDS = "isp_paused_session_ids"
CACHE_KEY_ALERT_SENT = "isp_outage_telegram_alert_sent"
OUTAGE_IDENTIFIER = "interrupted by our ISP"
OUTAGE_ANNOUNCEMENT_TEXT = (
    "⚠️ NOTICE: Internet is temporarily interrupted by our ISP. "
    "All user timers have been FROZEN to protect your remaining time! "
    "Your timer will automatically resume as soon as connection is restored."
)


def _safe_cache_get(key, default=None):
    try:
        return cache.get(key, default)
    except Exception:
        return default


def _safe_cache_set(key, value, timeout=300):
    try:
        cache.set(key, value, timeout=timeout)
    except Exception:
        pass


def _safe_cache_delete(key):
    try:
        cache.delete(key)
    except Exception:
        pass


def probe_upstream_internet(timeout=0.8):
    """
    Fast non-blocking TCP socket probe to upstream DNS endpoints (8.8.8.8, 1.1.1.1).
    Falls back to a single ping probe if TCP fails.
    Returns True if online, False if offline.
    """
    for host in ("8.8.8.8", "1.1.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host, 53))
            s.close()
            return True
        except Exception:
            continue

    # Fallback ping probe
    try:
        ping_cmd = (
            ["ping", "-n", "1", "-w", "1000", "8.8.8.8"]
            if platform.system() == "Windows"
            else ["ping", "-c", "1", "-W", "1", "8.8.8.8"]
        )
        res = subprocess.run(ping_cmd, capture_output=True, timeout=1.5)
        return res.returncode == 0
    except Exception:
        return False


def check_isp_internet_status(force_probe=False):
    """
    Main entry point for checking ISP connection.
    Uses short 5-second cache to prevent socket storms while ensuring near real-time updates.
    Handles auto-pause and auto-announcement based on SystemSettings.
    """
    from dashboard.models import Announcement, SystemSettings
    from sessions_app.models import Session
    from sessions_app import iptables

    settings_obj = SystemSettings.get_settings()

    if not settings_obj.enable_internet_check:
        return {
            "is_online": True,
            "isp_outage": False,
            "enable_outage_announcement": False,
            "enable_outage_auto_pause": False,
            "message": "",
        }

    # Check short-term cache
    if not force_probe:
        cached_status = _safe_cache_get(CACHE_KEY_STATUS)
        if cached_status is not None:
            return cached_status

    is_online = probe_upstream_internet(timeout=0.8)
    _safe_cache_set("internet_status_ok", is_online, timeout=120)

    existing_announcement = Announcement.objects.filter(
        is_active=True, message__contains=OUTAGE_IDENTIFIER
    ).first()
    existing_outage = existing_announcement is not None

    result = {
        "is_online": is_online,
        "isp_outage": False,
        "enable_outage_announcement": bool(settings_obj.enable_outage_announcement),
        "enable_outage_auto_pause": bool(settings_obj.enable_outage_auto_pause),
        "message": "",
    }

    if not is_online:
        fail_count = (_safe_cache_get(CACHE_KEY_FAIL_COUNT) or 0) + 1
        _safe_cache_set(CACHE_KEY_FAIL_COUNT, fail_count, timeout=300)

        # Confirmed outage if 2 consecutive fails or outage already flagged
        is_confirmed_outage = fail_count >= 2 or existing_outage
        result["isp_outage"] = is_confirmed_outage

        if is_confirmed_outage:
            logger.warning("ISP Outage active (probe offline, fail_count=%d)", fail_count)

            # 1. Auto Announcement Popup
            if settings_obj.enable_outage_announcement:
                result["message"] = OUTAGE_ANNOUNCEMENT_TEXT
                if not existing_outage:
                    Announcement.objects.filter(message__contains=OUTAGE_IDENTIFIER).delete()
                    Announcement.objects.create(message=OUTAGE_ANNOUNCEMENT_TEXT, is_active=True)
            elif existing_outage:
                # If announcement disabled by admin, remove old outage announcements
                Announcement.objects.filter(message__contains=OUTAGE_IDENTIFIER).delete()

            # 2. Auto-Pause Active Sessions
            if settings_obj.enable_outage_auto_pause:
                active_sessions = list(Session.objects.filter(status="active"))
                paused_ids = _safe_cache_get(CACHE_KEY_PAUSED_IDS) or []
                for s in active_sessions:
                    try:
                        s.pause_session()
                        try:
                            iptables.block_device(s.mac_address)
                        except Exception:
                            pass
                        if s.id not in paused_ids:
                            paused_ids.append(s.id)
                    except Exception as e:
                        logger.error("Failed to pause session %s during outage: %s", s.id, e)
                _safe_cache_set(CACHE_KEY_PAUSED_IDS, paused_ids, timeout=None)

            # 3. Telegram Outage Alert (sent once per outage event)
            if not _safe_cache_get(CACHE_KEY_ALERT_SENT):
                _safe_cache_set(CACHE_KEY_ALERT_SENT, True, timeout=3600)
                try:
                    from dashboard.telegram_bot import get_telegram_config, send_telegram_message
                    cfg = get_telegram_config()
                    if cfg.get("enabled") and cfg.get("notify_isp_down"):
                        paused_count = len(_safe_cache_get(CACHE_KEY_PAUSED_IDS) or [])
                        send_telegram_message(
                            f"🚨 *ISP OUTAGE DETECTED!*\n"
                            f"Upstream internet connection dropped.\n\n"
                            f"⏸ *Auto-Pause:* `{paused_count}` session(s) frozen.\n"
                            f"📢 Outage notice displayed on captive portal screen."
                        )
                except Exception as tg_err:
                    logger.warning("Failed to send Telegram outage alert: %s", tg_err)

    else:
        # Online — recover if previously offline
        _safe_cache_delete(CACHE_KEY_FAIL_COUNT)
        _safe_cache_delete(CACHE_KEY_ALERT_SENT)

        paused_ids = _safe_cache_get(CACHE_KEY_PAUSED_IDS) or []
        had_outage = existing_outage or len(paused_ids) > 0

        if had_outage:
            logger.info("ISP internet restored! Resuming student sessions...")

            # 1. Resume paused sessions
            resumed_count = 0
            if paused_ids:
                sessions_to_resume = Session.objects.filter(id__in=paused_ids, status="paused")
                for s in sessions_to_resume:
                    try:
                        s.resume_session()
                        try:
                            rate = int(s.plan.speed_limit * 1024) if (s.plan and s.plan.speed_limit) else None
                            iptables.allow_device(s.mac_address, rate_kbps=rate)
                        except Exception:
                            pass
                        resumed_count += 1
                    except Exception as e:
                        logger.error("Failed to resume session %s: %s", s.id, e)
                _safe_cache_delete(CACHE_KEY_PAUSED_IDS)

            # 2. Remove outage announcement
            Announcement.objects.filter(message__contains=OUTAGE_IDENTIFIER).delete()

            # 3. Telegram Recovery Alert
            try:
                from dashboard.telegram_bot import get_telegram_config, send_telegram_message
                cfg = get_telegram_config()
                if cfg.get("enabled") and cfg.get("notify_isp_down"):
                    send_telegram_message(
                        f"🟢 *ISP INTERNET RESTORED!*\n"
                        f"Upstream connection is back online.\n\n"
                        f"▶️ *Action Taken:* Resumed `{resumed_count}` student session(s).\n"
                        f"🧹 Captive portal outage popup cleared."
                    )
            except Exception as tg_err:
                logger.warning("Failed to send Telegram recovery alert: %s", tg_err)

            result["recovered"] = True
            result["resumed_count"] = resumed_count

    # Cache result for 5 seconds
    _safe_cache_set(CACHE_KEY_STATUS, result, timeout=5)
    return result
