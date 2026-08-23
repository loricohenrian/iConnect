"""
Captive portal views.
"""
import hmac
import re

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.core.paginator import Paginator

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
        "enable_family_pass": SystemSettings.get_settings().enable_family_pass,
        "family_pass_base_rate": SystemSettings.get_settings().family_pass_base_rate,
        "family_pass_device_rate": SystemSettings.get_settings().family_pass_device_rate,
        "family_pass_max_devices": SystemSettings.get_settings().family_pass_max_devices,
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
    ).select_related("plan", "session_group").first()

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
    
    # Calculate pause info for display
    if active_session.plan and active_session.plan.pause_limit > 0:
        context["pauses_left"] = max(0, active_session.plan.pause_limit - active_session.pause_count)
    else:
        context["pauses_left"] = "Unlimited"
        
    if active_session.plan and active_session.plan.pause_duration_limit > 0:
        context["pause_max_hours"] = active_session.plan.pause_duration_limit
    else:
        context["pause_max_hours"] = SystemSettings.get_settings().global_pause_limit_hours
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
    page_obj = None
    if history_verified:
        sessions_qs = Session.objects.filter(
            mac_address=mac_address,
        ).select_related("plan").order_by("-time_in")
        
        paginator = Paginator(sessions_qs, 10)
        page_number = request.GET.get("page")
        page_obj = paginator.get_page(page_number)
        sessions = page_obj.object_list

    announcements = Announcement.objects.filter(is_active=True)

    context = {
        "mac_address": mac_address,
        "request_ip": request_ip,
        "sessions": sessions,
        "page_obj": page_obj,
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
    prizes = list(SpinPrize.objects.filter(is_active=True).order_by('probability_weight'))
    
    if not prizes:
        can_spin = False
        error_message = "No prizes available."
        
    total_weight = sum(p.probability_weight for p in prizes)
    
    wheel_prizes = []
    current_deg = 0
    
    colors = ['#10B981', '#3B82F6', '#F59E0B', '#8B5CF6', '#EF4444', '#EC4899', '#14B8A6', '#84CC16', '#6366F1', '#D946EF']
    
    if prizes:
        for index, prize in enumerate(prizes):
            # Share of the circle
            deg_share = (prize.probability_weight / total_weight) * 360 if total_weight > 0 else 0
            end_deg = current_deg + deg_share
            mid_deg = current_deg + (deg_share / 2)
            
            wheel_prizes.append({
                'id': prize.id,
                'name': prize.name,
                'start_deg': round(current_deg, 2),
                'end_deg': round(end_deg, 2),
                'mid_deg': round(mid_deg, 2),
                'minutes': prize.minutes_reward,
                'color': colors[index % len(colors)]
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

def api_execute_spin(request):
    """API endpoint to execute a spin, deduct points, and award prize."""
    import json
    import random
    from django.http import JsonResponse
    from django.utils import timezone
    from django.db import transaction
    from dashboard.models import SystemSettings
    from sessions_app.models import DeviceProfile, SpinPrize, Session

    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)

    settings_obj = SystemSettings.get_settings()
    if not settings_obj.enable_spin_wheel:
        return JsonResponse({"status": "error", "message": "Spin wheel is disabled"})

    mac_address = _get_mac_address(request)
    if not mac_address:
        return JsonResponse({"status": "error", "message": "MAC address required"})

    try:
        with transaction.atomic():
            device_profile, _ = DeviceProfile.objects.select_for_update().get_or_create(mac_address=mac_address)

            today = timezone.localdate()
            if device_profile.last_spin_date != today:
                device_profile.spins_today = 0
                device_profile.last_spin_date = today

            # Validation — all checks BEFORE deducting points
            if device_profile.spins_today >= settings_obj.daily_spin_limit:
                return JsonResponse({"status": "error", "message": "Daily spin limit reached"})

            if device_profile.points < settings_obj.spin_cost_points:
                return JsonResponse({"status": "error", "message": "Not enough points"})

            prizes = list(SpinPrize.objects.filter(is_active=True))
            if not prizes:
                return JsonResponse({"status": "error", "message": "No prizes configured"})

            # Calculate weights and select prize
            total_weight = sum(p.probability_weight for p in prizes)
            if total_weight <= 0:
                return JsonResponse({"status": "error", "message": "Invalid prize configuration"})

            random_val = random.uniform(0, total_weight)

            current_weight = 0
            selected_prize = None

            for prize in prizes:
                current_weight += prize.probability_weight
                if random_val <= current_weight:
                    selected_prize = prize
                    break

            if not selected_prize:
                selected_prize = prizes[-1]

            # Calculate target_deg for the wheel animation
            sorted_prizes = sorted(prizes, key=lambda x: x.probability_weight)
            deg_current = 0
            target_deg = 0
            for prize in sorted_prizes:
                deg_share = (prize.probability_weight / total_weight) * 360
                if prize.id == selected_prize.id:
                    target_deg = deg_current + random.uniform(deg_share * 0.1, deg_share * 0.9)
                    break
                deg_current += deg_share

            # Deduct points and update spin count
            device_profile.points -= settings_obj.spin_cost_points
            device_profile.spins_today += 1
            device_profile.save(update_fields=['points', 'spins_today', 'last_spin_date'])

            # Award prize — extend existing session if one is active, or create a new one!
            prize_applied = False
            if selected_prize.minutes_reward > 0:
                session = Session.objects.filter(
                    mac_address=mac_address,
                    status__in=["active", "paused"]
                ).first()

                if session:
                    session.duration_minutes_purchased += selected_prize.minutes_reward
                    session.save(update_fields=['duration_minutes_purchased'])
                    prize_applied = True
                else:
                    # No active session, so we create a completely free one for the reward!
                    from sessions_app import iptables
                    # Attempt to get IP if function available in this scope, otherwise fallback
                    ip_address = ""
                    try:
                        from sessions_app.views import _client_ip
                        ip_address = _client_ip(request)
                    except ImportError:
                        pass

                    new_session = Session.objects.create(
                        mac_address=mac_address,
                        plan=None, # It's a free prize, not a paid plan
                        time_in=timezone.now(),
                        duration_minutes_purchased=selected_prize.minutes_reward,
                        amount_paid=0,
                        status="active",
                        ip_address=ip_address,
                        device_name="Spin Winner"
                    )
                    iptables.allow_device(mac_address)
                    prize_applied = True

            # Calculate remaining spins for the response
            remaining_spins = max(0, settings_obj.daily_spin_limit - device_profile.spins_today)

            return JsonResponse({
                "status": "success",
                "prize": {
                    "id": selected_prize.id,
                    "name": selected_prize.name,
                    "minutes": selected_prize.minutes_reward,
                    "mid_deg": target_deg,
                    "applied": prize_applied,
                },
                "updated": {
                    "points": device_profile.points,
                    "remaining_spins": remaining_spins,
                }
            })
    except Exception as e:
        return JsonResponse({"status": "error", "message": "An error occurred during spin processing"}, status=500)


def api_spin_data(request):
    """JSON API returning spin wheel data for the modal."""
    from django.http import JsonResponse
    from django.utils import timezone
    from dashboard.models import SystemSettings
    from sessions_app.models import DeviceProfile, SpinPrize

    settings_obj = SystemSettings.get_settings()

    if not settings_obj.enable_spin_wheel:
        return JsonResponse({"enabled": False})

    mac_address = _get_mac_address(request)
    if not mac_address:
        return JsonResponse({"enabled": True, "error": "MAC address required"})

    device_profile, _ = DeviceProfile.objects.get_or_create(mac_address=mac_address)

    today = timezone.localdate()
    spins_today = device_profile.spins_today if device_profile.last_spin_date == today else 0
    remaining_spins = max(0, settings_obj.daily_spin_limit - spins_today)

    can_spin = True
    error_message = ""

    if remaining_spins <= 0:
        can_spin = False
        error_message = "You have reached the daily spin limit."
    elif device_profile.points < settings_obj.spin_cost_points:
        can_spin = False
        error_message = "Not enough points to spin."

    prizes = list(SpinPrize.objects.filter(is_active=True).order_by('probability_weight'))

    if not prizes:
        can_spin = False
        error_message = "No prizes available."

    total_weight = sum(p.probability_weight for p in prizes)

    wheel_prizes = []
    current_deg = 0
    for prize in prizes:
        deg_share = (prize.probability_weight / total_weight) * 360 if total_weight > 0 else 0
        end_deg = current_deg + deg_share
        mid_deg = current_deg + (deg_share / 2)
        wheel_prizes.append({
            'id': prize.id,
            'name': prize.name,
            'start_deg': round(current_deg, 2),
            'end_deg': round(end_deg, 2),
            'mid_deg': round(mid_deg, 2),
            'minutes': prize.minutes_reward,
        })
        current_deg = end_deg

    return JsonResponse({
        "enabled": True,
        "can_spin": can_spin,
        "error_message": error_message,
        "points": device_profile.points,
        "streak": device_profile.current_streak,
        "spin_cost": settings_obj.spin_cost_points,
        "remaining_spins": remaining_spins,
        "daily_limit": settings_obj.daily_spin_limit,
        "prizes": wheel_prizes,
        "points_per_peso": settings_obj.points_per_peso,
        "points_per_streak": settings_obj.points_per_streak_day,
    })

