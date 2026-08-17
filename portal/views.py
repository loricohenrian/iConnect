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


def _mac_from_arp(ip_address):
    """Look up MAC address from Linux ARP table based on client IP."""
    if not ip_address or ip_address in ("unknown", "127.0.0.1", "::1"):
        return ""
    try:
        with open("/proc/net/arp", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[0] == ip_address:
                    mac = _normalize_mac(parts[3])
                    if mac:
                        return mac
    except (OSError, IOError):
        pass
    return ""


def _get_mac_address(request):
    stored_mac = _normalize_mac(request.session.get(SESSION_MAC_KEY, ""))
    query_mac = _normalize_mac(request.GET.get("mac", ""))

    if stored_mac and query_mac and query_mac != stored_mac:
        return stored_mac

    if query_mac and not stored_mac:
        request.session[SESSION_MAC_KEY] = query_mac
        return query_mac

    if stored_mac:
        return stored_mac

    # Auto-detect from ARP table if no MAC available yet
    client_ip = _client_ip(request)
    arp_mac = _mac_from_arp(client_ip)
    if arp_mac:
        request.session[SESSION_MAC_KEY] = arp_mac
        return arp_mac

    return ""


def _history_passcode_enabled():
    # Passcode disabled — sessions are already scoped to each device's MAC
    return False


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
            status__in=["active", "paused"],
        ).select_related("plan").first()

        if active_session and request_ip and active_session.ip_address != request_ip:
            active_session.ip_address = request_ip
            active_session.save(update_fields=["ip_address"])
            if active_session.status == "active":
                rate = active_session.plan.speed_limit if active_session.plan else None
                iptables.allow_device(mac_address, rate_kbps=rate)

    if active_session and active_session.time_remaining_seconds > 0:
        return redirect(f"/session/?mac={mac_address}")

    # Find the most picked plan (highest session count)
    from django.db.models import Count, Sum
    from sessions_app.models import CoinEvent
    most_popular = (
        Session.objects.values("plan_id")
        .annotate(pick_count=Count("id"))
        .order_by("-pick_count")
        .first()
    )
    most_popular_plan_id = most_popular["plan_id"] if most_popular else None

    # Calculate balance (unlinked coins for this device)
    balance = 0
    device_profile = None
    if mac_address:
        balance = CoinEvent.objects.filter(
            mac_address=mac_address,
            session__isnull=True,
        ).aggregate(total=Sum("amount"))["total"] or 0
        from sessions_app.models import DeviceProfile
        device_profile = DeviceProfile.record_connection(mac_address)

    # Connection slots
    from dashboard.models import SystemSettings; max_slots = SystemSettings.get_settings().max_concurrent_sessions
    active_count = Session.objects.filter(status='active').count()
    available_slots = max(0, max_slots - active_count)

    context = {
        "plans": plans,
        "announcements": announcements,
        "expired": expired,
        "is_whitelisted": is_whitelisted,
        "mac_address": mac_address,
        "mac_required": mac_required,
        "active_page": "home",
        "most_popular_plan_id": most_popular_plan_id,
        "balance": balance,
        "device_profile": device_profile,
        "slots_active": active_count,
        "slots_max": max_slots,
        "slots_available": available_slots,
        "insert_coin_countdown_seconds": SystemSettings.get_settings().insert_coin_countdown_seconds,
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
        status__in=["active", "paused"],
    ).select_related("plan").first()

    if active_session and request_ip and active_session.ip_address != request_ip:
        active_session.ip_address = request_ip
        active_session.save(update_fields=["ip_address"])
        if active_session.status == "active":
            rate = active_session.plan.speed_limit if active_session.plan else None
            iptables.allow_device(mac_address, rate_kbps=rate)

    if not active_session:
        return redirect(f"/?expired=1&mac={mac_address}")

    # Only expire active sessions (not paused) that ran out of time
    if active_session.status == "active" and active_session.time_remaining_seconds <= 0:
        active_session.expire_session()
        iptables.block_device(active_session.mac_address)
        return redirect(f"/?expired=1&mac={mac_address}")

    # Connection slots
    from dashboard.models import SystemSettings; max_slots = SystemSettings.get_settings().max_concurrent_sessions
    active_count = Session.objects.filter(status='active').count()
    available_slots = max(0, max_slots - active_count)
    
    from sessions_app.models import DeviceProfile
    device_profile = DeviceProfile.record_connection(mac_address)

    context = {
        "session": active_session,
        "announcements": announcements,
        "mac_address": mac_address,
        "time_remaining_seconds": int(active_session.time_remaining_seconds),
        "plans": Plan.objects.filter(is_active=True),
        "active_page": "home",
        "device_profile": device_profile,
        "system_settings": SystemSettings.get_settings(),
        "slots_active": active_count,
        "slots_max": max_slots,
        "slots_available": available_slots,
        "insert_coin_countdown_seconds": SystemSettings.get_settings().insert_coin_countdown_seconds,
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

    request_ip = _client_ip(request)
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
    """Public portal API for realtime announcements, plan updates, and connection slots."""
    plans = Plan.objects.filter(is_active=True).order_by("price", "id")
    announcements = Announcement.objects.filter(is_active=True).order_by("-created_at", "-id")

    from django.db.models import Count
    most_popular = (
        Session.objects.values("plan_id")
        .annotate(pick_count=Count("id"))
        .order_by("-pick_count")
        .first()
    )
    most_popular_plan_id = most_popular["plan_id"] if most_popular else None

    # Connection slots
    from django.conf import settings
    from dashboard.models import SystemSettings; max_slots = SystemSettings.get_settings().max_concurrent_sessions
    active_count = Session.objects.filter(status='active').count()
    available_slots = max(0, max_slots - active_count)

    plan_payload = [
        {
            "id": plan.id,
            "name": plan.name,
            "price": plan.price,
            "duration_minutes": plan.duration_minutes,
            "duration_display": plan.duration_display,
            "price_per_minute": float(plan.price_per_minute),
            "speed_limit": float(plan.speed_limit) if plan.speed_limit is not None else None,
            "speed_limit_upload": float(plan.speed_limit_upload) if plan.speed_limit_upload is not None else None,
            "is_most_popular": plan.id == most_popular_plan_id,
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
            "slots": {
                "active": active_count,
                "max": max_slots,
                "available": available_slots,
            },
            "meta": {
                "plan_count": len(plan_payload),
                "announcement_count": len(announcement_payload),
            },
        }
    )

def spin_wheel_view(request):
    """View to show the spin wheel game."""
    from dashboard.models import SystemSettings
    from sessions_app.models import DeviceProfile, SpinPrize
    
    settings = SystemSettings.get_settings()
    if not settings.enable_spin_wheel:
        return redirect("/")
        
    mac_address = _get_mac_address(request)
    if not mac_address:
        return redirect("/?mac_required=1")
        
    device_profile, _ = DeviceProfile.objects.get_or_create(mac_address=mac_address)
    
    # Check if they can spin
    can_spin = True
    error_message = ""
    
    # 1. Check daily limit
    from django.utils import timezone
    today = timezone.localdate()
    spins_today = device_profile.spins_today if device_profile.last_spin_date == today else 0
    remaining_spins = max(0, settings.daily_spin_limit - spins_today)
    
    if remaining_spins <= 0:
        can_spin = False
        error_message = "You have reached the daily spin limit."
    
    # 2. Check points
    elif device_profile.points < settings.spin_cost_points:
        can_spin = False
        error_message = "Not enough points to spin."
    
    # Calculate wheel segments based on prizes
    prizes = list(SpinPrize.objects.filter(is_active=True).order_by('weight'))
    
    if not prizes:
        can_spin = False
        error_message = "No prizes available."
        
    total_weight = sum(p.weight for p in prizes)
    
    wheel_prizes = []
    current_deg = 0
    
    if prizes:
        for prize in prizes:
            # Share of the circle
            deg_share = (prize.weight / total_weight) * 360 if total_weight > 0 else 0
            end_deg = current_deg + deg_share
            mid_deg = current_deg + (deg_share / 2)
            
            wheel_prizes.append({
                'id': prize.id,
                'name': prize.name,
                'start_deg': round(current_deg, 2),
                'end_deg': round(end_deg, 2),
                'mid_deg': round(mid_deg, 2),
                'minutes': prize.reward_minutes
            })
            current_deg = end_deg
        
    context = {
        'system_settings': settings,
        'device_profile': device_profile,
        'remaining_spins': remaining_spins,
        'can_spin': can_spin,
        'error_message': error_message,
        'prizes': wheel_prizes
    }
    
    return render(request, "portal/spin_wheel.html", context)

from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
def api_execute_spin(request):
    """API endpoint to execute a spin, deduct points, and award prize."""
    import json
    import random
    from django.http import JsonResponse
    from django.utils import timezone
    from dashboard.models import SystemSettings
    from sessions_app.models import DeviceProfile, SpinPrize, Session
    from .views import _get_mac_address, _client_ip
    
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)
        
    settings = SystemSettings.get_settings()
    if not settings.enable_spin_wheel:
        return JsonResponse({"status": "error", "message": "Spin wheel is disabled"})
        
    mac_address = _get_mac_address(request)
    if not mac_address:
        return JsonResponse({"status": "error", "message": "MAC address required"})
        
    device_profile, _ = DeviceProfile.objects.get_or_create(mac_address=mac_address)
    
    today = timezone.localdate()
    if device_profile.last_spin_date != today:
        device_profile.spins_today = 0
        device_profile.last_spin_date = today
        
    # Validation
    if device_profile.spins_today >= settings.daily_spin_limit:
        return JsonResponse({"status": "error", "message": "Daily spin limit reached"})
        
    if device_profile.points < settings.spin_cost_points:
        return JsonResponse({"status": "error", "message": "Not enough points"})
        
    prizes = list(SpinPrize.objects.filter(is_active=True))
    if not prizes:
        return JsonResponse({"status": "error", "message": "No prizes configured"})
        
    # Calculate weights and select prize
    total_weight = sum(p.weight for p in prizes)
    random_val = random.uniform(0, total_weight)
    
    current_weight = 0
    selected_prize = None
    
    for prize in prizes:
        current_weight += prize.weight
        if random_val <= current_weight:
            selected_prize = prize
            break
            
    if not selected_prize:
        selected_prize = prizes[-1]
        
    # Find mid_deg to pass back to frontend for wheel rotation
    current_deg = 0
    prize_mid_deg = 0
    sorted_prizes = sorted(prizes, key=lambda x: x.weight)
    
    for prize in sorted_prizes:
        deg_share = (prize.weight / total_weight) * 360
        if prize.id == selected_prize.id:
            prize_mid_deg = current_deg + (deg_share / 2)
        current_deg += deg_share
        
    # Deduct points and update spins
    device_profile.points -= settings.spin_cost_points
    device_profile.spins_today += 1
    device_profile.save()
    
    # Award prize
    if selected_prize.reward_minutes > 0:
        # Create or extend session
        session = Session.objects.filter(
            mac_address=mac_address,
            status__in=["active", "paused"]
        ).first()
        
        from dashboard.utils.mikrotik import allow_device
        if session:
            # Extend existing session
            session.duration_minutes_purchased += selected_prize.reward_minutes
            session.save(update_fields=['duration_minutes_purchased'])
            # Since the device is already active/paused, rules should already be fine, 
            # or if paused, it remains paused but with more time.
            if session.status == "active":
                rate = session.plan.speed_limit if session.plan else None
                allow_device(mac_address, rate_kbps=rate)
        else:
            # Create a free session
            import uuid
            new_session = Session.objects.create(
                session_id=str(uuid.uuid4())[:12],
                mac_address=mac_address,
                ip_address=_client_ip(request),
                duration_minutes_purchased=selected_prize.reward_minutes,
                amount_paid=0,
                status='active',
                is_free_spin=True
            )
            allow_device(mac_address)
            
    return JsonResponse({
        "status": "success",
        "prize": {
            "id": selected_prize.id,
            "name": selected_prize.name,
            "minutes": selected_prize.reward_minutes,
            "mid_deg": prize_mid_deg
        }
    })
