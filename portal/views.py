"""
Captive portal views.
"""
import hmac
import re

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render

from dashboard.models import Announcement
from sessions_app import iptables
from sessions_app.models import Plan, Session, WhitelistedDevice


SESSION_MAC_KEY = "portal_mac_address"
HISTORY_PASSCODE_VERIFIED_KEY = "portal_history_passcode_verified_for"
MAC_ADDRESS_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


def _normalize_mac(value):
    normalized = (value or "").strip().upper()
    if MAC_ADDRESS_RE.match(normalized):
        return normalized
    return ""


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _get_mac_address(request):
    stored_mac = _normalize_mac(request.session.get(SESSION_MAC_KEY, ""))
    query_mac = _normalize_mac(request.GET.get("mac", ""))

    if query_mac:
        request.session[SESSION_MAC_KEY] = query_mac
        return query_mac

    return stored_mac


def _bind_active_session_to_request(active_session, request_ip):
    """Keep active session aligned with request network identity."""
    if not active_session:
        return None

    if request_ip and active_session.ip_address != request_ip:
        active_session.ip_address = request_ip
        active_session.save(update_fields=["ip_address"])

    # Ensure paid access remains allowed across reconnects.
    iptables.allow_device(active_session.mac_address)
    return active_session


def _history_passcode_enabled():
    configured_passcode = str(getattr(settings, "PISONET_HISTORY_PASSCODE", "")).strip()
    flag_enabled = bool(getattr(settings, "PISONET_HISTORY_PASSCODE_ENABLED", True))
    return flag_enabled and bool(configured_passcode)


def index(request):
    """Plan selection page."""
    mac_address = _get_mac_address(request)
    mac_required = request.GET.get("mac_required") == "1"
    plans = Plan.objects.filter(is_active=True)
    announcements = Announcement.objects.filter(is_active=True)
    expired = request.GET.get("expired", False)

    is_whitelisted = False
    active_session = None
    request_ip = _client_ip(request)
    if mac_address:
        is_whitelisted = WhitelistedDevice.objects.filter(
            mac_address=mac_address
        ).exists()
        active_session = Session.objects.filter(
            mac_address=mac_address,
            status="active",
        ).select_related("plan").first()

    if active_session and active_session.time_remaining_seconds > 0:
        _bind_active_session_to_request(active_session, request_ip)
        return redirect(f"/session/?mac={mac_address}")

    context = {
        "plans": plans,
        "announcements": announcements,
        "expired": expired,
        "is_whitelisted": is_whitelisted,
        "mac_address": mac_address,
        "mac_required": mac_required,
        "show_dev_portal_flow": bool(
            getattr(settings, "PISONET_PORTAL_DEV_FLOW_ENABLED", settings.DEBUG)
        ),
        "active_page": "home",
    }
    return render(request, "portal/index.html", context)


def session_page(request):
    """Session timer page."""
    mac_address = _get_mac_address(request)
    if not mac_address:
        return redirect("/?mac_required=1")

    announcements = Announcement.objects.filter(is_active=True)
    request_ip = _client_ip(request)
    active_session = Session.objects.filter(
        mac_address=mac_address,
        status="active",
    ).select_related("plan").first()

    active_session = _bind_active_session_to_request(active_session, request_ip)

    if not active_session or active_session.time_remaining_seconds <= 0:
        if active_session:
            active_session.expire_session()
            iptables.block_device(active_session.mac_address)
        return redirect(f"/?expired=1&mac={mac_address}")

    context = {
        "session": active_session,
        "announcements": announcements,
        "mac_address": mac_address,
        "time_remaining_seconds": int(active_session.time_remaining_seconds),
        "active_page": "home",
    }
    return render(request, "portal/session.html", context)


def history(request):
    """Usage history for the current device."""
    mac_address = _get_mac_address(request)
    if not mac_address:
        return redirect("/?mac_required=1")

    passcode_required = _history_passcode_enabled()
    passcode_error = ""
    verified_for_mac = request.session.get(HISTORY_PASSCODE_VERIFIED_KEY, "")
    history_verified = (not passcode_required) or verified_for_mac == mac_address

    if request.method == "POST" and passcode_required:
        action = request.POST.get("action", "").strip()
        if action == "verify_history_passcode":
            submitted = request.POST.get("passcode", "").strip()
            configured = str(getattr(settings, "PISONET_HISTORY_PASSCODE", "")).strip()
            if configured and submitted and hmac.compare_digest(submitted, configured):
                request.session[HISTORY_PASSCODE_VERIFIED_KEY] = mac_address
                history_verified = True
            else:
                passcode_error = "Invalid passcode."
                history_verified = False
        elif action == "lock_history":
            request.session.pop(HISTORY_PASSCODE_VERIFIED_KEY, None)
            history_verified = False

    sessions = []
    if history_verified:
        sessions = Session.objects.filter(
            mac_address=mac_address,
        ).select_related("plan").order_by("-time_in")[:20]

    announcements = Announcement.objects.filter(is_active=True)

    context = {
        "sessions": sessions,
        "mac_address": mac_address,
        "announcements": announcements,
        "passcode_required": passcode_required,
        "history_verified": history_verified,
        "passcode_error": passcode_error,
        "active_page": "history",
    }
    return render(request, "portal/history.html", context)


def manual(request):
    """User guide / FAQ page."""
    context = {
        "announcements": Announcement.objects.filter(is_active=True),
        "mac_address": _get_mac_address(request),
        "active_page": "manual",
    }
    return render(request, "portal/manual.html", context)


def live_data(request):
    """Public portal API for realtime announcements and plan updates."""
    plans = Plan.objects.filter(is_active=True).order_by("price", "id")
    announcements = Announcement.objects.filter(is_active=True).order_by("-created_at", "-id")

    plan_payload = [
        {
            "id": plan.id,
            "name": plan.name,
            "price": plan.price,
            "duration_minutes": plan.duration_minutes,
            "duration_display": plan.duration_display,
            "price_per_minute": float(plan.price_per_minute),
            "speed_limit": float(plan.speed_limit) if plan.speed_limit is not None else None,
        }
        for plan in plans
    ]

    announcement_payload = [
        {
            "id": ann.id,
            "message": ann.message,
        }
        for ann in announcements
    ]

    return JsonResponse(
        {
            "plans": plan_payload,
            "announcements": announcement_payload,
            "meta": {
                "plan_count": len(plan_payload),
                "announcement_count": len(announcement_payload),
            },
        }
    )
