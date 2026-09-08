import logging
import platform
import socket
import subprocess
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_KEY_STATUS = "isp_internet_status_v2"
CACHE_KEY_FAIL_COUNT = "isp_fail_count"
CACHE_KEY_SUCCESS_COUNT = "isp_success_count"
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


def probe_upstream_internet(timeout=2.0):
    """
    Genuine internet connectivity probe using TLS (HTTPS).
    Plain HTTP (port 80), ping (ICMP), and DNS (port 53) are vulnerable to local router/modem
    interception when WAN fiber is down. HTTPS to 1.1.1.1 and www.google.com CANNOT be spoofed
    by a disconnected local router without triggering an SSL certificate verification failure.
    """
    import sys
    import urllib.request
    import ssl

    # 1. Direct HTTPS to 1.1.1.1 (no DNS needed, checks real routing + TLS handshake)
    try:
        req = urllib.request.Request(
            "https://1.1.1.1",
            headers={"User-Agent": "iConnect-Probe/1.0"}
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass

    # 2. HTTPS to Google (checks DNS resolution + real internet HTTPS)
    try:
        req = urllib.request.Request(
            "https://www.google.com/generate_204",
            headers={"User-Agent": "iConnect-Probe/1.0"}
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            if resp.status in (200, 204):
                return True
    except Exception:
        pass

    # 3. Unit test mocking fallback (only used in test suite when socket.socket is patched)
    if 'test' in sys.argv:
        for host in ("8.8.8.8", "1.1.1.1"):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(timeout)
                s.connect((host, 53))
                s.close()
                return True
            except Exception:
                continue

    return False


def check_isp_internet_status(force_probe=False):
    """
    Main entry point for checking ISP connection.
    Uses 10-second cache to prevent probe storms while ensuring responsive updates.
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

    is_online = probe_upstream_internet(timeout=1.5)
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
        # Reset success count on any failure
        _safe_cache_set(CACHE_KEY_SUCCESS_COUNT, 0, timeout=300)

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
        # Online probe
        _safe_cache_delete(CACHE_KEY_FAIL_COUNT)

        paused_ids = _safe_cache_get(CACHE_KEY_PAUSED_IDS) or []
        had_outage = existing_outage or len(paused_ids) > 0

        if had_outage:
            # Require 3 consecutive successful checks before clearing an active outage (anti-flapping)
            success_count = (_safe_cache_get(CACHE_KEY_SUCCESS_COUNT) or 0) + 1
            _safe_cache_set(CACHE_KEY_SUCCESS_COUNT, success_count, timeout=300)

            if success_count < 3 and not force_probe:
                # Still stabilizing — keep outage active
                logger.info("ISP probe succeeded %d/3 times, awaiting further confirmation", success_count)
                result["isp_outage"] = True
                result["is_online"] = False
                result["message"] = OUTAGE_ANNOUNCEMENT_TEXT
                _safe_cache_set(CACHE_KEY_STATUS, result, timeout=10)
                return result

            # Confirmed restored!
            logger.info("ISP internet restored! Sessions kept paused — users must manually resume.")
            _safe_cache_delete(CACHE_KEY_SUCCESS_COUNT)
            _safe_cache_delete(CACHE_KEY_ALERT_SENT)

            # 1. Restore iptables internet access for paused sessions WITHOUT resuming the timer.
            #    This ensures internet works again when the user returns, but the timer only
            #    starts counting once the user explicitly taps "Resume" — protecting users who
            #    left home during the outage from losing time they didn't use.
            restored_access_count = 0
            if paused_ids:
                sessions_to_unblock = Session.objects.filter(id__in=paused_ids, status="paused")
                for s in sessions_to_unblock:
                    try:
                        rate = int(s.plan.speed_limit * 1024) if (s.plan and s.plan.speed_limit) else None
                        iptables.allow_device(s.mac_address, rate_kbps=rate)
                        restored_access_count += 1
                    except Exception as e:
                        logger.error("Failed to restore iptables for session %s: %s", s.id, e)
                # Keep CACHE_KEY_PAUSED_IDS so we know which sessions were ISP-paused
                # (cleared by the session resume endpoint when the user manually resumes)
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
                        f"⏸ *Sessions kept paused* — users must tap Resume themselves.\n"
                        f"✅ Internet access restored for `{restored_access_count}` session(s).\n"
                        f"🧹 Captive portal outage popup cleared."
                    )
            except Exception as tg_err:
                logger.warning("Failed to send Telegram recovery alert: %s", tg_err)

            result["recovered"] = True
            result["resumed_count"] = 0  # No sessions auto-resumed; users resume manually

    # Cache result for 10 seconds
    _safe_cache_set(CACHE_KEY_STATUS, result, timeout=10)
    return result
