"""
Captive portal views.
"""
import hmac
import logging
import re

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.core.paginator import Paginator
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache

from dashboard.models import Announcement
from sessions_app import iptables
from sessions_app.models import Plan, Session, WhitelistedDevice

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger('audit')


SESSION_MAC_KEY = "portal_mac_address"
HISTORY_PASSCODE_VERIFIED_KEY = "portal_history_passcode_verified_for"
MAC_ADDRESS_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


def _portal_ip():
    """Get the canonical IP address of the captive portal router (defaults to 10.10.10.1)."""
    val = getattr(settings, "PISONET_PORTAL_IP", "")
    return str(val).strip() or "10.10.10.1"


def _normalize_mac(value):
    normalized = (value or "").strip().upper()
    if MAC_ADDRESS_RE.match(normalized):
        return normalized
    return ""


def _client_ip(request):
    real_ip = request.META.get("HTTP_X_REAL_IP", "")
    if real_ip:
        return real_ip.strip()
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
    # Auto-detect from kernel ARP table first (ground truth of the physical device connection)
    client_ip = _client_ip(request)
    arp_mac = _mac_from_arp(client_ip)
    if arp_mac:
        request.session[SESSION_MAC_KEY] = arp_mac
        return arp_mac

    # Fallback for environments where ARP is unavailable (e.g. tests, loopback, proxy)
    stored_mac = _normalize_mac(request.session.get(SESSION_MAC_KEY, ""))
    query_mac = _normalize_mac(request.GET.get("mac", "") or request.GET.get("mac_address", ""))
    header_mac = _normalize_mac(request.META.get("HTTP_X_MAC_ADDRESS", ""))

    post_mac = ""
    if request.method == "POST":
        post_mac = _normalize_mac(request.POST.get("mac_address", "") or request.POST.get("mac", ""))
        if not post_mac and request.body:
            try:
                import json
                body_data = json.loads(request.body.decode('utf-8'))
                if isinstance(body_data, dict):
                    post_mac = _normalize_mac(body_data.get("mac_address", "") or body_data.get("mac", ""))
            except Exception:
                pass

    explicit_mac = query_mac or header_mac or post_mac

    if explicit_mac:
        request.session[SESSION_MAC_KEY] = explicit_mac
        return explicit_mac

    if stored_mac:
        return stored_mac

    return ""


def _history_passcode_enabled():
    # Passcode disabled — sessions are already scoped to each device's MAC
    return False


def _get_most_popular_plan_id():
    """
    Find the most popular active plan.
    Calculates the plan with the highest session count over the last 30 days (rolling 30-day window).
    Falls back to all-time active sessions if no sessions exist in the last 30 days.
    Only considers active plans, excluding spin prize plans.
    """
    from datetime import timedelta
    from django.db.models import Count

    # 1. Rolling 30-day window
    last_30_days = timezone.now() - timedelta(days=30)
    most_popular = (
        Session.objects.filter(
            plan__isnull=False,
            plan__is_active=True,
            time_in__gte=last_30_days,
        )
        .exclude(plan__name__startswith="Prize:")
        .values("plan_id")
        .annotate(pick_count=Count("id"))
        .order_by("-pick_count")
        .first()
    )
    if most_popular and most_popular.get("plan_id"):
        return most_popular["plan_id"]

    # 2. Fallback to all-time active sessions if no sessions in the last 30 days
    most_popular_all_time = (
        Session.objects.filter(
            plan__isnull=False,
            plan__is_active=True,
        )
        .exclude(plan__name__startswith="Prize:")
        .values("plan_id")
        .annotate(pick_count=Count("id"))
        .order_by("-pick_count")
        .first()
    )
    return most_popular_all_time["plan_id"] if most_popular_all_time else None


@never_cache
def index(request):
    """Plan selection page."""
    mac_address = _get_mac_address(request)

    # Redirect to portal IP if accessed via intercepted external host (e.g. connectivitycheck.gstatic.com)
    portal_ip = _portal_ip()
    host = request.get_host().split(':')[0]
    if host not in (portal_ip, '127.0.0.1', 'localhost', 'testserver'):
        redirect_url = f'http://{portal_ip}/'
        if mac_address:
            redirect_url += f'?mac={mac_address}'
        return redirect(redirect_url)

    mac_required = request.GET.get("mac_required") == "1"
    plans = Plan.objects.filter(is_active=True).order_by("price", "id")
    announcements = Announcement.objects.filter(is_active=True).exclude(message__contains="interrupted by our ISP").exclude(message__contains="automatically resume")
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

    if active_session and active_session.time_remaining_seconds <= 1:
        active_session.expire_session()
        iptables.block_device(active_session.mac_address)
        active_session = None

    if active_session and active_session.time_remaining_seconds > 1:
        host = request.get_host()
        base = "" if host in ("10.10.10.1", "127.0.0.1", "localhost") else "http://10.10.10.1"
        return redirect(f"{base}/session/?mac={mac_address}")

    # Find the most picked plan (highest session count)
    from django.db.models import Sum
    from sessions_app.models import CoinEvent
    most_popular_plan_id = _get_most_popular_plan_id()

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

    # Connection slots & Internet Check
    from dashboard.models import SystemSettings
    from django.core.cache import cache

    settings_obj = SystemSettings.get_settings()
    max_slots = settings_obj.max_concurrent_sessions
    active_count = Session.objects.filter(status='active').count()
    available_slots = max(0, max_slots - active_count)

    from sessions_app.internet_monitor import check_isp_internet_status
    isp_info = check_isp_internet_status(force_probe=False)
    is_internet_offline = isp_info.get("isp_outage", False)

    from sessions_app.views import generate_smart_combo_examples
    smart_combo_examples = generate_smart_combo_examples(plans, is_extend=False)

    context = {
        "plans": plans,
        "smart_combo_examples": smart_combo_examples,
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
        "insert_coin_countdown_seconds": settings_obj.insert_coin_countdown_seconds,
        "is_internet_offline": is_internet_offline,
    }
    return render(request, "portal/index.html", context)


@never_cache
def session_page(request):
    """Session timer page."""
    mac_address = _get_mac_address(request)

    portal_ip = _portal_ip()
    host = request.get_host().split(':')[0]
    if host not in (portal_ip, '127.0.0.1', 'localhost', 'testserver'):
        redirect_url = f'http://{portal_ip}/session/'
        if mac_address:
            redirect_url += f'?mac={mac_address}'
        return redirect(redirect_url)

    if not mac_address:
        return redirect("/?mac_required=1")

    announcements = Announcement.objects.filter(is_active=True).exclude(message__contains="interrupted by our ISP").exclude(message__contains="automatically resume")
    request_ip = _client_ip(request)
    active_session = Session.objects.filter(
        mac_address=mac_address,
        status__in=["active", "paused"],
    ).select_related("plan", "session_group").first()

    from sessions_app.internet_monitor import check_isp_internet_status
    isp_info = check_isp_internet_status(force_probe=False)
    isp_outage = isp_info.get("isp_outage", False)

    if active_session:
        # Check if ISP is currently down and auto-pause is enabled
        if isp_outage and isp_info.get("enable_outage_auto_pause", True) and active_session.status == "active":
            active_session.pause_session()
            try:
                iptables.block_device(active_session.mac_address)
            except Exception:
                pass
            try:
                from django.core.cache import cache as dj_cache
                dj_cache.set(f"manual_pause_{active_session.id}", True, timeout=86400 * 7)
                dj_cache.delete(f"auto_paused_{active_session.id}")
            except Exception:
                pass
            active_session.refresh_from_db()

    if active_session and request_ip and active_session.ip_address != request_ip:
        active_session.ip_address = request_ip
        active_session.save(update_fields=["ip_address"])
        if active_session.status == "active":
            rate = active_session.plan.speed_limit if active_session.plan else None
            iptables.allow_device(mac_address, rate_kbps=rate)

    if not active_session:
        return redirect(f"/?expired=1&mac={mac_address}")

    # Expire sessions that ran out of time or exceeded pause limit
    if active_session.time_remaining_seconds <= 1:
        active_session.expire_session()
        iptables.block_device(active_session.mac_address)
        return redirect(f"/?expired=1&mac={mac_address}")

    # Connection slots
    from dashboard.models import SystemSettings; max_slots = SystemSettings.get_settings().max_concurrent_sessions
    active_count = Session.objects.filter(status='active').count()
    available_slots = max(0, max_slots - active_count)
    
    from sessions_app.models import DeviceProfile
    device_profile = DeviceProfile.record_connection(mac_address)

    group_code_remaining_display = None
    group_code_remaining_seconds = 0
    if active_session and active_session.session_group and active_session.session_group.code_expires_at:
        rem_sec = max(0, int((active_session.session_group.code_expires_at - timezone.now()).total_seconds()))
        group_code_remaining_seconds = rem_sec
        if rem_sec <= 0:
            group_code_remaining_display = "Expired"
        else:
            h = rem_sec // 3600
            m = (rem_sec % 3600) // 60
            s = rem_sec % 60
            if h >= 24:
                d = h // 24
                group_code_remaining_display = f"{d}d {h % 24}h {m}m"
            else:
                group_code_remaining_display = f"{h}h {m}m {s}s"

    plans = Plan.objects.filter(is_active=True).order_by("price", "id")
    most_popular_plan_id = _get_most_popular_plan_id()
    from sessions_app.views import generate_smart_combo_examples
    smart_combo_examples = generate_smart_combo_examples(plans, is_extend=True)

    context = {
        "session": active_session,
        "announcements": announcements,
        "mac_address": mac_address,
        "time_remaining_seconds": int(active_session.time_remaining_seconds),
        "plans": plans,
        "most_popular_plan_id": most_popular_plan_id,
        "smart_combo_examples": smart_combo_examples,
        "active_page": "home",
        "device_profile": device_profile,
        "system_settings": SystemSettings.get_settings(),
        "slots_active": active_count,
        "slots_max": max_slots,
        "slots_available": available_slots,
        "insert_coin_countdown_seconds": SystemSettings.get_settings().insert_coin_countdown_seconds,
        "group_code_remaining_display": group_code_remaining_display,
        "group_code_remaining_seconds": group_code_remaining_seconds,
        "isp_outage": isp_outage,
        "enable_outage_announcement": isp_info.get("enable_outage_announcement", True),
        "enable_outage_auto_pause": isp_info.get("enable_outage_auto_pause", True),
        "outage_message": isp_info.get("message", ""),
    }
    
    # Calculate pause info for display
    context["pauses_left"] = active_session.pauses_left
        
    if active_session.plan and active_session.plan.pause_duration_limit > 0:
        context["pause_max_hours"] = active_session.plan.pause_duration_limit
    else:
        context["pause_max_hours"] = SystemSettings.get_settings().global_pause_limit_hours
    return render(request, "portal/session.html", context)


@never_cache
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

    announcements = Announcement.objects.filter(is_active=True).exclude(message__contains="interrupted by our ISP")

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


@never_cache
def manual(request):
    """User guide / FAQ page."""
    context = {
        "announcements": Announcement.objects.filter(is_active=True).exclude(message__contains="interrupted by our ISP"),
        "mac_address": _get_mac_address(request),
        "active_page": "manual",
    }
    return render(request, "portal/manual.html", context)


@never_cache
def live_data(request):
    """Public portal API for realtime announcements, plan updates, and connection slots."""
    plans = Plan.objects.filter(is_active=True).order_by("price", "id")
    announcements = Announcement.objects.filter(is_active=True).exclude(message__contains="interrupted by our ISP").exclude(message__contains="automatically resume").order_by("-created_at", "-id")

    most_popular_plan_id = _get_most_popular_plan_id()

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

    from sessions_app.views import generate_smart_combo_examples
    from sessions_app.internet_monitor import check_isp_internet_status
    isp_info = check_isp_internet_status(force_probe=False)

    smart_combos = generate_smart_combo_examples(plans, is_extend=False)
    smart_combos_extend = generate_smart_combo_examples(plans, is_extend=True)

    mac_address = _get_mac_address(request)
    group_pass_payload = None
    if mac_address:
        user_session = Session.objects.filter(
            mac_address__iexact=mac_address,
            status__in=["active", "paused"]
        ).order_by("-id").first()
        if user_session and user_session.session_group_id:
            from sessions_app.models import SessionGroup
            grp = SessionGroup.objects.filter(id=user_session.session_group_id).first()
            if grp:
                actual_redeemed = max(grp.redeemed_count, grp.sessions.count())
                group_pass_payload = {
                    "code": grp.group_code,
                    "redeemed": actual_redeemed,
                    "max": grp.max_devices,
                    "status": grp.status,
                    "code_expires_at": grp.code_expires_at.isoformat() if grp.code_expires_at else None,
                }

    return JsonResponse(
        {
            "plans": plan_payload,
            "smart_combo_examples": smart_combos,
            "smart_combo_examples_extend": smart_combos_extend,
            "announcements": announcement_payload,
            "isp_outage": isp_info.get("isp_outage", False),
            "enable_outage_announcement": isp_info.get("enable_outage_announcement", True),
            "enable_outage_auto_pause": isp_info.get("enable_outage_auto_pause", True),
            "outage_message": isp_info.get("message", ""),
            "group_pass": group_pass_payload,
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
    
    from sessions_app.models import SuspiciousDevice
    if SuspiciousDevice.objects.filter(mac_address=mac_address, is_blocked=True).exists():
        can_spin = False
        error_message = "Your device has been blocked by the administrator."
    elif remaining_spins <= 0:
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

@csrf_exempt
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

    from sessions_app.models import SuspiciousDevice
    if SuspiciousDevice.objects.filter(mac_address=mac_address, is_blocked=True).exists():
        return JsonResponse({"status": "error", "message": "Your device has been blocked by the administrator."}, status=403)

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
                    update_fields = ['duration_minutes_purchased']
                    if selected_prize.pause_limit is not None:
                        if selected_prize.pause_limit == 0:
                            session.pause_limit = 0
                        else:
                            session.add_pauses(selected_prize.pause_limit)
                        update_fields.append('pause_limit')
                    session.save(update_fields=update_fields)
                    prize_applied = True
                else:
                    # No active session, so we create a completely free one for the reward!
                    from sessions_app import iptables
                    from sessions_app.models import Plan
                    
                    # Create a hidden Plan to hold the prize's network and pause limits
                    hidden_plan, _ = Plan.objects.get_or_create(
                        name=f"Prize: {selected_prize.name}",
                        price=0,
                        duration_minutes=selected_prize.minutes_reward,
                        defaults={
                            'speed_limit': selected_prize.speed_limit,
                            'speed_limit_upload': selected_prize.speed_limit_upload,
                            'pause_limit': selected_prize.pause_limit,
                            'pause_duration_limit': selected_prize.pause_duration_limit,
                            'is_active': False
                        }
                    )
                    # Update limits in case the admin changed them
                    hidden_plan.speed_limit = selected_prize.speed_limit
                    hidden_plan.speed_limit_upload = selected_prize.speed_limit_upload
                    hidden_plan.pause_limit = selected_prize.pause_limit
                    hidden_plan.pause_duration_limit = selected_prize.pause_duration_limit
                    hidden_plan.is_active = False
                    hidden_plan.save()

                    # Attempt to get IP if function available in this scope, otherwise fallback
                    ip_address = ""
                    try:
                        from sessions_app.views import _client_ip
                        ip_address = _client_ip(request)
                    except ImportError:
                        pass

                    prev_session = Session.objects.filter(mac_address=mac_address).exclude(device_name="Spin Winner").exclude(device_name="").order_by("-time_in").first()
                    dev_name = prev_session.device_name if prev_session and prev_session.device_name else "Spin Winner"

                    new_session = Session.objects.create(
                        mac_address=mac_address,
                        plan=hidden_plan, # Apply the prize's network limits
                        time_in=timezone.now(),
                        duration_minutes_purchased=selected_prize.minutes_reward,
                        amount_paid=0,
                        status="active",
                        ip_address=ip_address,
                        device_name=dev_name
                    )
                    
                    rate_kbps = int(selected_prize.speed_limit * 1024) if selected_prize.speed_limit else None
                    upload_kbps = int(selected_prize.speed_limit_upload * 1024) if selected_prize.speed_limit_upload else rate_kbps
                    
                    try:
                        iptables.allow_device(mac_address, rate_kbps=rate_kbps, upload_kbps=upload_kbps)
                    except Exception as ipt_err:
                        logger.warning("iptables allow_device warning during spin prize award: %s", ipt_err)
                    prize_applied = True

            # Calculate remaining spins for the response
            remaining_spins = max(0, settings_obj.daily_spin_limit - device_profile.spins_today)

            return JsonResponse({
                "status": "success",
                "prize": {
                    "id": selected_prize.id,
                    "name": selected_prize.name,
                    "minutes": selected_prize.minutes_reward,
                    "mid_deg": round(target_deg, 2),
                    "applied": prize_applied,
                    "type": "minutes" if selected_prize.minutes_reward > 0 else "none",
                    "points": 0,
                },
                "target_deg": round(target_deg, 2),
                "remaining_points": device_profile.points,
                "remaining_spins": remaining_spins,
                "applied_to_session": prize_applied,
                "updated": {
                    "points": device_profile.points,
                    "remaining_spins": remaining_spins,
                }
            })
    except Exception as e:
        logger.exception("Error executing spin: %s", e)
        return JsonResponse({"status": "error", "message": "An error occurred during spin processing"}, status=500)


def api_spin_data(request):
    """JSON API returning spin wheel data for the modal."""
    from django.http import JsonResponse
    from django.utils import timezone
    from dashboard.models import SystemSettings
    from sessions_app.models import DeviceProfile, SpinPrize, SuspiciousDevice

    settings_obj = SystemSettings.get_settings()

    if not settings_obj.enable_spin_wheel:
        return JsonResponse({"enabled": False})

    mac_address = _get_mac_address(request)
    if not mac_address:
        return JsonResponse({"enabled": True, "error": "MAC address required"})

    if SuspiciousDevice.objects.filter(mac_address=mac_address, is_blocked=True).exists():
        return JsonResponse({"enabled": False, "is_blocked": True, "error": "Your device has been blocked by the administrator."})

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


@require_POST
def api_report_issue(request):
    """
    Submit customer issue report or feedback.
    POST /api/report-issue/
    Body: JSON or Form with { "category": "...", "message": "...", "contact_info": "...", "mac_address": "..." }
    """
    import json
    from dashboard.models import IssueReport
    from django.core.cache import cache

    ip = _client_ip(request)
    rate_limit_key = f"issue_report_rate_{ip}"
    report_count = 0
    try:
        report_count = cache.get(rate_limit_key, 0) or 0
    except Exception:
        report_count = 0

    if report_count >= 5:
        return JsonResponse(
            {"error": "Too many reports submitted. Please wait a few minutes."},
            status=429,
        )

    data = {}
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body.decode("utf-8"))
        except Exception:
            data = {}
    else:
        data = request.POST

    message = (data.get("message") or "").strip()
    category = (data.get("category") or "other").strip()
    contact_info = (data.get("contact_info") or "").strip()[:100]
    mac_address = _normalize_mac(data.get("mac_address") or _get_mac_address(request))

    if not message:
        return JsonResponse({"error": "Please provide a description of the issue."}, status=400)

    # Deduplication: prevent identical rapid clicks within 30s
    import hashlib
    msg_hash = hashlib.md5(f"{message}_{category}_{mac_address}".encode("utf-8")).hexdigest()
    dedup_key = f"issue_report_dedup_{ip}_{msg_hash}"
    try:
        if cache.get(dedup_key):
            return JsonResponse({
                "status": "success",
                "message": "Your report has already been received. Thank you!",
            })
    except Exception:
        pass

    valid_categories = dict(IssueReport.CATEGORY_CHOICES).keys()
    if category not in valid_categories:
        category = "other"

    report = IssueReport.objects.create(
        mac_address=mac_address,
        contact_info=contact_info,
        category=category,
        message=message[:2000],
        status="pending",
    )

    try:
        cache.set(rate_limit_key, report_count + 1, timeout=300)
        cache.set(dedup_key, True, timeout=30)
    except Exception:
        pass

    audit_logger.info(
        "event=issue_reported report_id=%s category=%s mac=%s ip=%s",
        report.id, category, mac_address or "<none>", ip,
    )

    try:
        from dashboard.telegram_bot import send_telegram_message, get_telegram_config, escape_markdown
        cfg = get_telegram_config()
        if cfg.get('enabled') and cfg.get('notify_tickets'):
            category_name = dict(IssueReport.CATEGORY_CHOICES).get(category, category)
            esc_cat = escape_markdown(category_name)
            esc_msg = escape_markdown(report.message)
            esc_mac = escape_markdown(report.mac_address or 'Unknown MAC')
            esc_contact = escape_markdown(report.contact_info or 'None')
            t_msg = (
                f"🚨 *New Support Ticket #{report.id}*\n\n"
                f"📂 *Category:* {esc_cat}\n"
                f"📝 *Message:* _{esc_msg}_\n"
                f"📱 *Device:* `{esc_mac}`\n"
                f"📞 *Contact:* `{esc_contact}`\n"
                f"🕒 *Time:* {timezone.now().strftime('%I:%M %p')}\n\n"
                f"Type /tickets on Telegram or view in Admin Console."
            )
            send_telegram_message(t_msg)
    except Exception as e:
        logger.warning(f"Failed to dispatch Telegram issue alert: {e}")

    return JsonResponse({
        "status": "success",
        "message": "Your report has been sent to the operator. Thank you!",
        "report_id": report.id,
    })


def captive_portal_probe(request):
    """
    Handle OS network connectivity detection probes (Android/Chrome, Apple, Windows, Firefox).
    If the device has an active session, return the exact OS success response (e.g. HTTP 204 or Success).
    If the device is not authenticated, redirect to the captive portal to prompt login/coin insertion.
    Always adds 'Connection: close' to prevent clients from reusing stale keep-alive sockets.
    """
    mac = _get_mac_address(request)
    client_ip = _client_ip(request)

    is_active = False
    if mac:
        is_active = Session.objects.filter(mac_address=mac, status='active').exists()
    elif client_ip:
        is_active = Session.objects.filter(ip_address=client_ip, status='active').exists()

    path = request.path.lower()

    if is_active:
        if 'generate_204' in path or 'gen_204' in path:
            response = HttpResponse(status=204)
        elif 'hotspot-detect' in path:
            response = HttpResponse(
                '<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>',
                content_type='text/html'
            )
        elif 'connecttest' in path:
            response = HttpResponse('Microsoft Connect Test', content_type='text/plain')
        elif 'ncsi' in path:
            response = HttpResponse('Microsoft NCSI', content_type='text/plain')
        elif 'success' in path:
            response = HttpResponse('success\n', content_type='text/plain')
        else:
            response = HttpResponse(status=204)
    else:
        # Not active — redirect to captive portal at portal IP explicitly
        portal_ip = _portal_ip()
        redirect_url = f'http://{portal_ip}/'
        if mac:
            redirect_url += f'?mac={mac}'
        response = redirect(redirect_url)

    # Prevent connection keep-alive and caching on all probe endpoints
    response['Connection'] = 'close'
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


def captive_portal_api(request):
    """
    RFC 8908 / RFC 7710 Captive Portal API endpoint.
    Queried natively by Android 11+ and iOS 14+ to automatically detect captive portal state
    and validate network connectivity in real time without manual user sign-in clicks.
    """
    mac = _get_mac_address(request)
    client_ip = _client_ip(request)

    sess = None
    if mac:
        sess = Session.objects.filter(mac_address=mac, status='active').order_by('-id').first()
    if not sess and client_ip and client_ip not in ("unknown", "127.0.0.1", "::1"):
        sess = Session.objects.filter(ip_address=client_ip, status='active').order_by('-id').first()

    is_active = False
    remaining_seconds = 0
    if sess and sess.time_remaining_seconds > 0:
        is_active = True
        remaining_seconds = int(sess.time_remaining_seconds)

    portal_ip = _portal_ip()
    data = {
        "captive": not is_active,
        "user-portal-url": f"http://{portal_ip}/session/" if is_active else f"http://{portal_ip}/",
        "venue-info-url": f"http://{portal_ip}/",
        "seconds-remaining": remaining_seconds if is_active else 0,
        "can-extend-session": is_active,
    }

    response = JsonResponse(data)
    response['Content-Type'] = 'application/captive+json'
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    return response




