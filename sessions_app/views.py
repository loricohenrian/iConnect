"""
Session management API views.
"""
from datetime import timedelta
import hmac
import logging
import subprocess

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import iptables
from .bandwidth import refresh_session_bandwidth_usage
from .models import (
    CoinEvent,
    CoinInsertRequest,
    DeviceProfile,
    Plan,
    Session,
    SessionGroup,
    SuspiciousDevice,
    WhitelistedDevice,
    PurchaseTransaction,
)
from .serializers import (
    CoinInsertedSerializer,
    PlanSerializer,
    SessionExtendSerializer,
    SessionSerializer,
    SessionStartSerializer,
    GroupJoinSerializer,
    WhitelistedDeviceSerializer,
)


PESO_SYMBOL = "\u20b1"
logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("audit")


def _is_dashboard_admin(user):
    return user.is_authenticated and user.is_staff


def _require_dashboard_admin_response(request):
    if _is_dashboard_admin(request.user):
        return None
    return Response({"detail": "Authentication required."}, status=status.HTTP_401_UNAUTHORIZED)


def _has_valid_device_api_key(request):
    expected = getattr(settings, "PISONET_DEVICE_API_KEY", "").strip()
    provided = request.headers.get("X-DEVICE-API-KEY", "").strip()

    if not expected or not provided:
        return False
    return hmac.compare_digest(provided, expected)


def _client_ip(request):
    # Nginx securely sets X-Real-IP to the actual client connection IP.
    # Ignoring X-Forwarded-For prevents header spoofing by clients.
    real_ip = request.META.get("HTTP_X_REAL_IP", "")
    if real_ip:
        return real_ip.strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _coin_rate_limited(request):
    ip = _client_ip(request)
    window_seconds = getattr(settings, "PISONET_COIN_WINDOW_SECONDS", 60)
    max_requests = getattr(settings, "PISONET_COIN_MAX_REQUESTS", 120)
    key = f"coin-inserted:{ip}"

    count = cache.get(key, 0)
    if count >= max_requests:
        return True

    cache.add(key, 0, timeout=window_seconds)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window_seconds)
    return False


def _session_extend_rate_limited(request, mac_address):
    ip = _client_ip(request)
    window_seconds = getattr(settings, "PISONET_VOUCHER_WINDOW_SECONDS", 300)
    max_attempts = getattr(settings, "PISONET_VOUCHER_MAX_ATTEMPTS", 8)
    key = f"session-extend:{ip}:{mac_address}"

    count = cache.get(key, 0)
    if count >= max_attempts:
        return True

    cache.add(key, 0, timeout=window_seconds)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window_seconds)
    return False


def _session_ip_matches_request(session, request):
    if not session:
        return False
    request_ip = _client_ip(request)
    if request_ip and session.ip_address != request_ip:
        from django.core.cache import cache
        key = f"mac_ip_changes:{session.mac_address}"
        changes = cache.get(key, 0)
        
        # If IP changes more than 2 times in 5 minutes, flag for MAC spoofing
        if changes >= 2:
            from .models import SuspiciousDevice
            SuspiciousDevice.record_incident(
                session.mac_address,
                ip_address=request_ip,
                reason="MAC Spoofing Suspected",
                evidence=f"MAC flapped between multiple IPs rapidly (old={session.ip_address}, new={request_ip})."
            )
        cache.set(key, changes + 1, timeout=300)

        session.ip_address = request_ip
        session.save(update_fields=["ip_address"])
        if session.status == "active":
            rate = session.plan.speed_limit if session.plan else None
            iptables.allow_device(session.mac_address, rate_kbps=rate)
        audit_logger.info(
            "event=session_ip_synced mac=%s new_ip=%s",
            session.mac_address,
            request_ip,
        )
    return True


def _extract_device_name(request, passed_name=None, mac_address=None):
    """
    Extract a friendly device name from passed value, previous sessions, or User-Agent.
    """
    if passed_name and passed_name.strip() and passed_name.strip() != "Unknown":
        return passed_name.strip()[:100]

    # Check if a recognized device name exists for this MAC
    if mac_address:
        prev = Session.objects.filter(mac_address=mac_address).exclude(
            device_name__in=["", "Unknown", None]
        ).order_by("-id").first()
        if prev and prev.device_name:
            return prev.device_name

    ua = request.META.get("HTTP_USER_AGENT", "")
    if ua:
        if "iPhone" in ua:
            return "iPhone"
        elif "iPad" in ua:
            return "iPad"
        elif "Android" in ua:
            import re
            m = re.search(r'Android[^;]*;\s*([^;)]+)', ua)
            if m:
                model = m.group(1).strip()
                return model[:100] if model else "Android"
            return "Android"
        elif "Windows" in ua:
            return "Windows PC"
        elif "Macintosh" in ua:
            return "Mac"
        elif "Linux" in ua:
            return "Linux"

    return "Unknown"


def _public_read_rate_limited(request, scope):
    ip = _client_ip(request)
    window_seconds = getattr(settings, "PISONET_PUBLIC_WINDOW_SECONDS", 60)
    max_requests = getattr(settings, "PISONET_PUBLIC_MAX_REQUESTS", 180)
    key = f"public-read:{scope}:{ip}"

    count = cache.get(key, 0)
    if count >= max_requests:
        return True

    cache.add(key, 0, timeout=window_seconds)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window_seconds)
    return False


def _pending_coin_events_for_mac(mac_address):
    if not mac_address:
        return CoinEvent.objects.none()
    return CoinEvent.objects.filter(
        session__isnull=True,
        mac_address__iexact=str(mac_address).strip(),
    ).order_by("timestamp", "id")


def _ensure_firewall_ready_for_session_start():
    """Ensure baseline firewall policy is enforced before starting a session."""
    if not getattr(settings, "PISONET_REQUIRE_FORWARD_DROP_BEFORE_SESSION", True):
        return True
    return iptables.enforce_firewall_baseline()


def _coin_request_window_seconds():
    from dashboard.models import SystemSettings
    settings_obj = SystemSettings.get_settings()
    return max(15, int(settings_obj.insert_coin_countdown_seconds))


def _coin_request_max_queue():
    return max(1, int(getattr(settings, "PISONET_COIN_REQUEST_MAX_QUEUE", 20)))


def _activate_next_coin_request(now=None):
    """Ensure only one active shared-slot request at a time."""
    now = now or timezone.now()
    window_seconds = _coin_request_window_seconds()

    with transaction.atomic():
        CoinInsertRequest.objects.select_for_update().filter(
            status=CoinInsertRequest.STATUS_ACTIVE,
            expires_at__isnull=False,
            expires_at__lte=now,
        ).update(status=CoinInsertRequest.STATUS_EXPIRED)

        active_request = CoinInsertRequest.objects.select_for_update().filter(
            status=CoinInsertRequest.STATUS_ACTIVE
        ).order_by("created_at", "id").first()
        if active_request:
            return active_request

        next_request = CoinInsertRequest.objects.select_for_update().filter(
            status=CoinInsertRequest.STATUS_PENDING
        ).order_by("created_at", "id").first()
        if not next_request:
            return None

        next_request.status = CoinInsertRequest.STATUS_ACTIVE
        next_request.activated_at = now
        next_request.expires_at = now + timedelta(seconds=window_seconds)
        next_request.save(update_fields=["status", "activated_at", "expires_at"])
        return next_request


def _coin_request_queue_position(coin_request):
    if coin_request.status not in (CoinInsertRequest.STATUS_PENDING, CoinInsertRequest.STATUS_ACTIVE):
        return None

    queue_ids = list(
        CoinInsertRequest.objects.filter(
            status__in=[CoinInsertRequest.STATUS_ACTIVE, CoinInsertRequest.STATUS_PENDING]
        ).order_by("created_at", "id").values_list("id", flat=True)
    )
    try:
        return queue_ids.index(coin_request.id) + 1
    except ValueError:
        return None


def _coin_request_payload(coin_request):
    if not coin_request:
        return None

    return {
        "id": coin_request.id,
        "purpose": coin_request.purpose,
        "status": coin_request.status,
        "mac_address": coin_request.mac_address,
        "expected_amount": coin_request.expected_amount,
        "credited_amount": coin_request.credited_amount,
        "queue_position": _coin_request_queue_position(coin_request),
        "expires_at": coin_request.expires_at,
        "is_group_pass": coin_request.is_group_pass,
        "group_pass_devices": coin_request.group_pass_devices,
        "plan_id": coin_request.plan_id,
        "ready_to_start": (
            coin_request.status in (CoinInsertRequest.STATUS_ACTIVE, CoinInsertRequest.STATUS_COMPLETED) and
            coin_request.expected_amount > 0 and 
            coin_request.credited_amount >= coin_request.expected_amount
        ),
    }


def _sync_coin_request_progress(coin_request):
    """Sync credited amount and transition request status when needed."""
    now = timezone.now()
    credited_amount = _pending_coin_events_for_mac(coin_request.mac_address).aggregate(
        total=Sum("amount")
    )["total"] or 0

    update_fields = []
    transitioned = False

    if coin_request.credited_amount != credited_amount:
        coin_request.credited_amount = credited_amount
        update_fields.append("credited_amount")

    if (
        coin_request.status == CoinInsertRequest.STATUS_ACTIVE
        and coin_request.expires_at
        and coin_request.expires_at <= now
    ):
        coin_request.status = CoinInsertRequest.STATUS_EXPIRED
        update_fields.append("status")
        transitioned = True

    if (
        coin_request.status == CoinInsertRequest.STATUS_COMPLETED
        and credited_amount < coin_request.expected_amount
    ):
        coin_request.status = CoinInsertRequest.STATUS_CANCELLED
        update_fields.append("status")

    if update_fields:
        # Avoid duplicate field entries in update_fields.
        coin_request.save(update_fields=list(dict.fromkeys(update_fields)))

    if transitioned:
        _activate_next_coin_request(now=now)

    return coin_request


def _active_coin_request_for_unscoped_insert():
    """Pick the currently active queue request for unscoped coin inserts."""
    active_request = _activate_next_coin_request()
    if not active_request:
        return None

    active_request = _sync_coin_request_progress(active_request)
    if active_request.status == CoinInsertRequest.STATUS_ACTIVE:
        return active_request

    return _activate_next_coin_request()


def _get_or_create_start_coin_request(mac_address, ip_address, plan, is_group_pass=False, group_pass_devices=1):
    """Create/reuse a queued start-session coin request for this device."""
    existing_request = CoinInsertRequest.objects.filter(
        mac_address=mac_address,
        purpose=CoinInsertRequest.PURPOSE_START,
        status__in=[CoinInsertRequest.STATUS_PENDING, CoinInsertRequest.STATUS_ACTIVE],
    ).order_by("created_at", "id").first()

    if existing_request:
        # If same plan, reuse. If different plan, cancel and create new.
        if existing_request.plan_id == plan.id and existing_request.is_group_pass == is_group_pass and existing_request.group_pass_devices == group_pass_devices:
            return _sync_coin_request_progress(existing_request), False
        else:
            existing_request.status = CoinInsertRequest.STATUS_CANCELLED
            existing_request.completed_at = timezone.now()
            existing_request.save(update_fields=["status", "completed_at"])

    queue_depth = CoinInsertRequest.objects.filter(
        status__in=[CoinInsertRequest.STATUS_PENDING, CoinInsertRequest.STATUS_ACTIVE]
    ).count()
    if queue_depth >= _coin_request_max_queue():
        raise ValueError("Coin request queue is full. Please try again shortly.")

    credited_amount = _pending_coin_events_for_mac(mac_address).aggregate(total=Sum("amount"))["total"] or 0
    initial_status = (
        CoinInsertRequest.STATUS_COMPLETED
        if credited_amount >= (plan.price * group_pass_devices if is_group_pass else plan.price)
        else CoinInsertRequest.STATUS_PENDING
    )

    coin_request = CoinInsertRequest.objects.create(
        mac_address=mac_address,
        ip_address=ip_address,
        purpose=CoinInsertRequest.PURPOSE_START,
        plan=plan,
        expected_amount=plan.price * group_pass_devices if is_group_pass else plan.price,
        is_group_pass=is_group_pass,
        group_pass_devices=group_pass_devices,
        credited_amount=credited_amount,
        status=initial_status,
        completed_at=timezone.now() if initial_status == CoinInsertRequest.STATUS_COMPLETED else None,
    )

    if coin_request.status == CoinInsertRequest.STATUS_PENDING:
        _activate_next_coin_request()
        coin_request.refresh_from_db()

    return _sync_coin_request_progress(coin_request), True


@api_view(["POST"])
@permission_classes([AllowAny])
def coin_inserted(request):
    """
    Receives coin pulse data from GPIO script.
    POST /api/coin-inserted/
    """
    if _coin_rate_limited(request):
        audit_logger.warning("event=coin_rate_limited ip=%s", _client_ip(request))
        return Response(
            {"error": "Too many coin requests. Please retry shortly."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    if not _has_valid_device_api_key(request):
        audit_logger.warning("event=coin_unauthorized ip=%s", _client_ip(request))
        return Response(
            {"error": "Unauthorized coin source"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    serializer = CoinInsertedSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    amount = serializer.validated_data["amount"]
    denomination = serializer.validated_data["denomination"]
    mac_address = serializer.validated_data.get("mac_address")
    if mac_address:
        mac_address = mac_address.upper().strip()
    assigned_request = None

    if not mac_address:
        assigned_request = _active_coin_request_for_unscoped_insert()
        if assigned_request:
            mac_address = assigned_request.mac_address.upper().strip()

    session = None
    voucher_code = None
    if mac_address:
        session = Session.objects.filter(
            mac_address__iexact=mac_address,
            status="active",
        ).first()

    # When a coin request exists (extend flow), DON'T link coin to the session.
    # _pending_coin_events_for_mac filters session__isnull=True, so linked
    # coins would be invisible to the coin request progress tracker.
    # If no active request and no mac, coin is saved as unassigned revenue (pure profit).
    coin_event = CoinEvent.objects.create(
        amount=amount,
        denomination=denomination,
        mac_address=mac_address,
        session=session if not assigned_request else None,
    )
    audit_logger.info(
        "event=coin_received amount=%s denomination=%s mac=%s request_id=%s ip=%s",
        amount,
        denomination,
        mac_address or "<unassigned>",
        assigned_request.id if assigned_request else "<none>",
        _client_ip(request),
    )

    if assigned_request:
        assigned_request = _sync_coin_request_progress(assigned_request)

    # Only create voucher session if there's an active session AND no coin request
    # (i.e., user inserted coins without going through the UI flow).
    # When a coin request exists, coins credit to it for the extend flow.
    if session and not assigned_request:
        plan = Plan.objects.filter(price=amount, is_active=True).first()
        if plan:
            voucher_code = Session.generate_voucher_code()
            Session.objects.create(
                mac_address=mac_address,
                plan=plan,
                duration_minutes_purchased=plan.duration_minutes,
                amount_paid=amount,
                status="paused",
                voucher_code=voucher_code,
            )
            DeviceProfile.add_spending_points(mac_address, amount)

    return Response(
        {
            "status": "success",
            "message": f"{PESO_SYMBOL}{amount} coin received" if assigned_request or mac_address else f"{PESO_SYMBOL}{amount} unassigned coin logged",
            "coin_event_id": coin_event.id,
            "voucher_code": voucher_code,
            "amount": amount,
            "assigned_mac_address": mac_address,
            "coin_request": _coin_request_payload(assigned_request),
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def coinslot_status(request):
    """
    Check if the physical coinslot should be enabled (active request in queue).
    Used by GPIO script / relay controller to enable or inhibit the coin selector.
    """
    active_req = _active_coin_request_for_unscoped_insert()
    if active_req:
        now = timezone.now()
        remaining_seconds = 0
        if active_req.expires_at and active_req.expires_at > now:
            remaining_seconds = int((active_req.expires_at - now).total_seconds())
        return Response({
            "status": "success",
            "enabled": True,
            "active_request_id": active_req.id,
            "mac_address": active_req.mac_address,
            "remaining_seconds": remaining_seconds,
            "expires_at": active_req.expires_at,
        })
    return Response({
        "status": "success",
        "enabled": False,
        "active_request_id": None,
        "mac_address": None,
        "remaining_seconds": 0,
        "expires_at": None,
    })


@api_view(["POST"])
@permission_classes([AllowAny])
def session_start_request(request):
    """Create/retrieve a queued coin-insert request for starting a session."""
    serializer = SessionStartSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    mac_address = serializer.validated_data["mac_address"]
    plan_id = serializer.validated_data.get("plan_id")
    is_group_pass = serializer.validated_data.get("is_group_pass", False)
    group_pass_devices = serializer.validated_data.get("group_pass_devices")
    group_pass_duration_minutes = serializer.validated_data.get("group_pass_duration_minutes")
    ip_address = _client_ip(request)

    from dashboard.models import SystemSettings
    from django.core.cache import cache
    
    settings_obj = SystemSettings.get_settings()
    if settings_obj.enable_internet_check:
        # Check if internet is online from cache
        if cache.get("internet_status_ok") is False:
            return Response(
                {"error": "Internet Connection is Offline. Coin slot disabled."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    if SuspiciousDevice.objects.filter(mac_address=mac_address, is_blocked=True).exists():
        audit_logger.warning(
            "event=session_request_blocked_device mac=%s ip=%s",
            mac_address, ip_address,
        )
        return Response(
            {"error": "Your device has been blocked by the administrator."},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response(
            {"error": "Plan not found or inactive"},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Note: We allow coin requests even if there's an active session,
    # because the user may want to extend their session with more coins.

    try:
        coin_request, created = _get_or_create_start_coin_request(
            mac_address, ip_address, plan,
            is_group_pass=is_group_pass,
            group_pass_devices=group_pass_devices
        )
    except ValueError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)

    if coin_request.status == CoinInsertRequest.STATUS_COMPLETED:
        message = "Payment is already sufficient. Tap Connect to start your session."
    elif coin_request.status == CoinInsertRequest.STATUS_ACTIVE:
        message = "Insert coins now. Your device currently owns the coin slot window."
    elif coin_request.status == CoinInsertRequest.STATUS_PENDING:
        message = "Request queued. Wait until your turn, then insert coins."
    else:
        message = "Request is no longer active. Please request again."

    return Response(
        {
            "status": "success",
            "message": message,
            "coin_request": _coin_request_payload(coin_request),
        },
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def session_start_request_status(request):
    """Return progress for a queued start-session coin request."""
    request_id = request.query_params.get("request_id", "").strip()
    if not request_id.isdigit():
        return Response(
            {"error": "request_id parameter is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    coin_request = CoinInsertRequest.objects.filter(
        id=int(request_id),
        purpose=CoinInsertRequest.PURPOSE_START,
    ).first()
    if not coin_request:
        return Response(
            {"error": "Coin request not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    mac_address = request.query_params.get("mac_address", "").upper().strip()
    if mac_address and mac_address != coin_request.mac_address:
        return Response(
            {"error": "Coin request belongs to another device"},
            status=status.HTTP_403_FORBIDDEN,
        )

    coin_request = _sync_coin_request_progress(coin_request)
    if coin_request.status == CoinInsertRequest.STATUS_PENDING:
        _activate_next_coin_request()
        coin_request.refresh_from_db()
        coin_request = _sync_coin_request_progress(coin_request)

    return Response(
        {
            "status": "success",
            "coin_request": _coin_request_payload(coin_request),
        }
    )

@api_view(["POST"])
@permission_classes([AllowAny])
def session_start_cancel(request):
    """Cancel a pending start-session coin request."""
    mac_address = request.data.get("mac_address", "").upper().strip()
    if not mac_address:
        return Response({"error": "MAC address required"}, status=status.HTTP_400_BAD_REQUEST)
        
    coin_request = CoinInsertRequest.objects.filter(
        mac_address=mac_address,
        status__in=[CoinInsertRequest.STATUS_PENDING, CoinInsertRequest.STATUS_ACTIVE]
    ).first()
    
    if coin_request:
        coin_request.status = CoinInsertRequest.STATUS_CANCELLED
        coin_request.completed_at = timezone.now()
        coin_request.save(update_fields=["status", "completed_at"])
        
        # In case the cancelled request was ACTIVE, activate the next one in queue
        _activate_next_coin_request()
        
    return Response({"status": "success"})


@api_view(["POST"])
@permission_classes([AllowAny])
def session_start(request):
    """
    Creates new session after payment.
    POST /api/session/start/
    """
    serializer = SessionStartSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    mac_address = serializer.validated_data["mac_address"]
    plan_id = serializer.validated_data.get("plan_id")
    is_group_pass = serializer.validated_data.get("is_group_pass", False)
    group_pass_devices = serializer.validated_data.get("group_pass_devices")
    group_pass_duration_minutes = serializer.validated_data.get("group_pass_duration_minutes")
    ip_address = _client_ip(request)
    device_name = serializer.validated_data.get("device_name")

    from dashboard.models import SystemSettings
    from django.core.cache import cache
    
    settings_obj = SystemSettings.get_settings()
    if settings_obj.enable_internet_check:
        if cache.get("internet_status_ok") is False:
            return Response(
                {"error": "Internet Connection is Offline. Coin slot disabled."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    if SuspiciousDevice.objects.filter(mac_address=mac_address, is_blocked=True).exists():
        audit_logger.warning("event=session_start_blocked_device mac=%s ip=%s", mac_address, ip_address)
        return Response(
            {"error": "Your device has been blocked by the administrator."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Auto-detect device name
    device_name = _extract_device_name(request, device_name, mac_address)

    plan = None
    expected_amount = 0
    duration_minutes = 0
    
    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
        expected_amount = plan.price * group_pass_devices if is_group_pass else plan.price
        duration_minutes = plan.duration_minutes
    except Plan.DoesNotExist:
        return Response({"error": "Plan not found or inactive"}, status=status.HTTP_404_NOT_FOUND)

    existing = Session.objects.filter(mac_address=mac_address, status="active").first()
    if existing:
        if ip_address and existing.ip_address != ip_address:
            existing.ip_address = ip_address
            existing.save(update_fields=["ip_address"])
            if existing.plan:
                rate = existing.plan.speed_limit
                iptables.allow_device(mac_address, rate_kbps=rate)
            elif existing.session_group:
                dl_kbps = int(existing.plan.speed_limit * 1024) if existing.plan and existing.plan.speed_limit else None
                ul_kbps = int(existing.plan.speed_limit_upload * 1024) if existing.plan and existing.plan.speed_limit_upload else dl_kbps
                iptables.allow_device(mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps)
        return Response(
            {"error": "Device already has an active session", "session": SessionSerializer(existing).data},
            status=status.HTTP_409_CONFLICT,
        )

    if not _ensure_firewall_ready_for_session_start():
        return Response({"error": "Firewall baseline is not ready. Please retry shortly."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    max_sessions = getattr(settings, "PISONET_MAX_CONCURRENT_SESSIONS", 20)
    active_count = Session.objects.filter(status="active").count()
    if active_count >= max_sessions:
        return Response({"error": f"Maximum concurrent users ({max_sessions}) reached. Please try again later."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    total_coins = _pending_coin_events_for_mac(mac_address).aggregate(total=Sum("amount"))["total"] or 0

    if total_coins < expected_amount:
        try:
            coin_request, _ = _get_or_create_start_coin_request(
                mac_address, ip_address, plan,
                is_group_pass=is_group_pass,
                group_pass_devices=group_pass_devices
            )
        except ValueError as exc:
            return Response({"error": str(exc), "required": expected_amount, "received": total_coins}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        return Response(
            {"error": f"Insufficient payment for {mac_address}. Need {PESO_SYMBOL}{expected_amount}, received {PESO_SYMBOL}{total_coins}", "required": expected_amount, "received": total_coins, "coin_request": _coin_request_payload(coin_request)},
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )

    try:
        with transaction.atomic():
            multiplier = total_coins // expected_amount if expected_amount > 0 else 1
            amount_paid = expected_amount * multiplier
            actual_duration = duration_minutes * multiplier
            
            session_group = None
            if is_group_pass:
                import random
                import string
                # Generate unique code
                while True:
                    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
                    if not SessionGroup.objects.filter(group_code=code).exists():
                        break

                # Determine code expiry from admin setting (0 = no expiry)
                expiry_hours = getattr(settings_obj, 'group_code_expiry_hours', 24)
                code_expires_at = None
                if expiry_hours > 0:
                    code_expires_at = timezone.now() + timedelta(hours=expiry_hours)

                session_group = SessionGroup.objects.create(
                    group_code=code,
                    plan=plan,
                    max_devices=group_pass_devices,
                    redeemed_count=1,
                    total_price=amount_paid,
                    duration_minutes=plan.duration_minutes if plan else actual_duration,
                    time_in=timezone.now(),
                    code_expires_at=code_expires_at,
                    status="active"
                )

            session = Session.objects.create(
                mac_address=mac_address,
                plan=plan,
                session_group=session_group,
                time_in=timezone.now(),
                duration_minutes_purchased=actual_duration,
                amount_paid=amount_paid,
                ip_address=ip_address,
                device_name=device_name,
                status="active",
            )

            PurchaseTransaction.objects.create(
                session=session,
                plan=plan,
                amount=amount_paid,
            )
            if is_group_pass and session_group and session_group.max_devices > 0:
                # Pro-rate points for the host (1 slot worth)
                host_points_value = session_group.total_price / session_group.max_devices
                DeviceProfile.add_spending_points(mac_address, host_points_value)
            else:
                DeviceProfile.add_spending_points(mac_address, amount_paid)

            used_amount = 0
            for event in _pending_coin_events_for_mac(mac_address):
                if used_amount >= amount_paid:
                    break
                event.session = session
                event.save(update_fields=["session"])
                used_amount += event.amount

            overpayment = used_amount - amount_paid
            if overpayment > 0:
                CoinEvent.objects.create(
                    mac_address=mac_address,
                    amount=overpayment,
                    denomination=overpayment if overpayment in (1, 5, 10, 20) else 1,
                    session=None,
                )

            CoinInsertRequest.objects.filter(
                mac_address=mac_address,
                purpose=CoinInsertRequest.PURPOSE_START,
                status__in=[CoinInsertRequest.STATUS_PENDING, CoinInsertRequest.STATUS_ACTIVE],
            ).update(
                status=CoinInsertRequest.STATUS_CANCELLED,
                completed_at=timezone.now(),
            )

            dl_kbps = None
            ul_kbps = None
            if plan:
                dl_kbps = int(plan.speed_limit * 1024) if plan.speed_limit else None
                ul_kbps = int(plan.speed_limit_upload * 1024) if plan.speed_limit_upload else dl_kbps

            if not iptables.allow_device(mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps):
                raise RuntimeError("Failed to allow internet access for this device")
    except RuntimeError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    _activate_next_coin_request()

    return Response(
        {
            "status": "success",
            "message": "Session started",
            "session": SessionSerializer(session).data,
            "session_group": session.session_group.group_code if session.session_group else None
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def session_join_group(request):
    """
    Redeem a Group Pass code.
    POST /api/session/join-group/
    Each redemption starts a fully independent session with the full plan duration —
    identical to a solo session of that plan.
    """
    serializer = GroupJoinSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    mac_address = serializer.validated_data["mac_address"]
    group_code = serializer.validated_data["group_code"].upper()
    ip_address = _client_ip(request)
    device_name = _extract_device_name(request, serializer.validated_data.get("device_name"), mac_address)

    # Rate limiting
    from django.core.cache import cache
    cache_key = f"join_group_attempts_{ip_address}"
    attempts = cache.get(cache_key, 0)
    if attempts >= 5:
        return Response({"error": "Too many attempts. Please try again later."}, status=status.HTTP_429_TOO_MANY_REQUESTS)
    cache.set(cache_key, attempts + 1, timeout=60)

    if SuspiciousDevice.objects.filter(mac_address=mac_address, is_blocked=True).exists():
        return Response({"error": "Your device has been blocked by the administrator."}, status=status.HTTP_403_FORBIDDEN)

    # Check if there is an existing active or paused session
    existing_session = Session.objects.filter(mac_address=mac_address, status__in=["active", "paused"]).first()

    if not _ensure_firewall_ready_for_session_start():
        return Response({"error": "Firewall is not ready. Please retry shortly."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    max_sessions = getattr(settings, "PISONET_MAX_CONCURRENT_SESSIONS", 20)
    if not existing_session and Session.objects.filter(status="active").count() >= max_sessions:
        return Response({"error": "Network is currently full."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    group = SessionGroup.objects.filter(group_code=group_code, status="active").select_related("plan").first()
    if not group:
        return Response({"error": "Invalid or expired group code."}, status=status.HTTP_404_NOT_FOUND)

    # Check code's own expiry (separate from session expiry)
    if group.is_code_expired():
        group.status = "expired"
        group.save(update_fields=["status"])
        return Response({"error": "This group pass has expired."}, status=status.HTTP_400_BAD_REQUEST)

    group_plan = group.plan
    if not group_plan:
        return Response({"error": "This group pass has no plan configured. Please contact the operator."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            # Lock the group row to prevent race conditions from concurrent clicks
            locked_group = SessionGroup.objects.select_for_update().get(id=group.id)
            
            # Check if all slots are used
            if locked_group.is_full():
                return Response(
                    {"error": f"This group pass is full ({locked_group.redeemed_count}/{locked_group.max_devices} slots used)."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # MAC lock — prevent same device from redeeming twice
            if locked_group.has_mac_redeemed(mac_address):
                return Response(
                    {"error": "Your device has already redeemed this group pass."},
                    status=status.HTTP_409_CONFLICT,
                )

            if existing_session:
                # Extend existing active or paused session
                existing_session.duration_minutes_purchased += group_plan.duration_minutes
                existing_session.session_group = locked_group
                if ip_address:
                    existing_session.ip_address = ip_address
                existing_session.save(update_fields=["duration_minutes_purchased", "session_group", "ip_address"])
                session = existing_session

                if session.status == "active":
                    dl_kbps = int(group_plan.speed_limit * 1024) if group_plan.speed_limit else None
                    ul_kbps = int(group_plan.speed_limit_upload * 1024) if group_plan.speed_limit_upload else dl_kbps
                    if not iptables.allow_device(mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps):
                        raise RuntimeError("Failed to update firewall rules for this device")
            else:
                # Each device gets the FULL plan duration — fully independent session
                session = Session.objects.create(
                    mac_address=mac_address,
                    plan=group_plan,
                    session_group=locked_group,
                    time_in=timezone.now(),
                    duration_minutes_purchased=group_plan.duration_minutes,
                    amount_paid=0,
                    ip_address=ip_address,
                    device_name=device_name,
                    status="active",
                )

                dl_kbps = int(group_plan.speed_limit * 1024) if group_plan.speed_limit else None
                ul_kbps = int(group_plan.speed_limit_upload * 1024) if group_plan.speed_limit_upload else dl_kbps

                if not iptables.allow_device(mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps):
                    raise RuntimeError("Failed to allow internet access for this device")

            # Increment redeemed count atomically (row is locked)
            locked_group.redeemed_count += 1

            # If all slots are now filled, mark the group exhausted
            if locked_group.redeemed_count >= locked_group.max_devices:
                locked_group.status = "exhausted"
                
            locked_group.save(update_fields=["redeemed_count", "status"])

            # Grant points to the joining user (pro-rated value of 1 slot)
            if locked_group.max_devices > 0:
                slot_value = locked_group.total_price / locked_group.max_devices
                DeviceProfile.add_spending_points(mac_address, slot_value)

    except RuntimeError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    # Clear rate limit on success
    cache.delete(cache_key)

    is_extension = bool(existing_session)
    msg = (
        f"Group pass redeemed! +{group_plan.duration_display} added to your session."
        if is_extension
        else "Successfully joined group pass. Your session has started."
    )

    return Response(
        {
            "status": "success",
            "message": msg,
            "session": SessionSerializer(session).data,
            "session_group": group.group_code,
            "extended": is_extension,
        },
        status=status.HTTP_200_OK if is_extension else status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def session_extend(request):
    """
    Extends session via voucher code.
    POST /api/session/extend/
    """
    serializer = SessionExtendSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    voucher_code = serializer.validated_data["voucher_code"].upper()
    mac_address = serializer.validated_data["mac_address"]

    if _session_extend_rate_limited(request, mac_address):
        audit_logger.warning(
            "event=session_extend_rate_limited mac=%s ip=%s",
            mac_address,
            _client_ip(request),
        )
        return Response(
            {"error": "Too many voucher attempts. Please try again later."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    try:
        voucher_session = Session.objects.get(
            voucher_code=voucher_code,
            status="paused",
        )
    except Session.DoesNotExist:
        audit_logger.warning(
            "event=session_extend_invalid_voucher mac=%s voucher=%s ip=%s",
            mac_address,
            voucher_code,
            _client_ip(request),
        )
        return Response(
            {"error": "Invalid or expired voucher code"},
            status=status.HTTP_404_NOT_FOUND,
        )

    expiry_minutes = getattr(settings, "PISONET_VOUCHER_EXPIRY_MINUTES", 5)
    if (timezone.now() - voucher_session.created_at).total_seconds() > expiry_minutes * 60:
        voucher_session.status = "expired"
        voucher_session.save(update_fields=["status"])
        return Response(
            {"error": "Voucher code has expired"},
            status=status.HTTP_410_GONE,
        )

    if voucher_session.mac_address and voucher_session.mac_address != mac_address:
        audit_logger.warning(
            "event=session_extend_mac_mismatch voucher_mac=%s request_mac=%s voucher=%s ip=%s",
            voucher_session.mac_address,
            mac_address,
            voucher_code,
            _client_ip(request),
        )
        return Response(
            {"error": "This voucher code belongs to a different device"},
            status=status.HTTP_403_FORBIDDEN,
        )

    active_session = Session.objects.filter(
        mac_address=mac_address,
        status="active",
    ).first()

    if active_session:
        _session_ip_matches_request(active_session, request)

    if active_session:
        active_session.extend_session(voucher_session.duration_minutes_purchased)
        active_session.amount_paid += voucher_session.amount_paid
        
        # Speed Always Wins logic
        speed_changed = False
        if voucher_session.plan and active_session.plan:
            new_dl = voucher_session.plan.speed_limit or 0
            old_dl = active_session.plan.speed_limit or 0
            if new_dl > old_dl:
                active_session.plan = voucher_session.plan
                speed_changed = True
        elif voucher_session.plan and not active_session.plan:
            active_session.plan = voucher_session.plan
            speed_changed = True
            
        update_fields = ["duration_minutes_purchased", "amount_paid", "status", "time_in"]
        if speed_changed:
            update_fields.append("plan")

        if voucher_session.plan:
            v_limit = voucher_session.plan.pause_limit
            if v_limit == 0:
                if active_session.pause_count > 0:
                    active_session.pause_count = 0
                    update_fields.append("pause_count")
            elif v_limit > 0 and active_session.pause_count > 0:
                active_session.pause_count = max(0, active_session.pause_count - v_limit)
                update_fields.append("pause_count")

        active_session.save(update_fields=update_fields)

        if speed_changed and active_session.plan:
            dl_kbps = int(active_session.plan.speed_limit * 1024) if active_session.plan.speed_limit else None
            ul_kbps = int(active_session.plan.speed_limit_upload * 1024) if active_session.plan.speed_limit_upload else dl_kbps
            iptables.apply_bandwidth_limit(mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps)

        voucher_session.status = "expired"
        voucher_session.save(update_fields=["status"])

        cache.delete(f"session-extend:{_client_ip(request)}:{mac_address}")
        audit_logger.info(
            "event=session_extend_success mode=active_session mac=%s voucher=%s ip=%s",
            mac_address,
            voucher_code,
            _client_ip(request),
        )

        return Response(
            {
                "status": "success",
                "message": f"Session extended by {voucher_session.duration_minutes_purchased} minutes",
                "session": SessionSerializer(active_session).data,
            }
        )

    if not _ensure_firewall_ready_for_session_start():
        audit_logger.error(
            "event=session_extend_firewall_baseline_failed mac=%s ip=%s",
            mac_address,
            _client_ip(request),
        )
        return Response(
            {"error": "Firewall baseline is not ready. Please retry shortly."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    try:
        with transaction.atomic():
            voucher_session.mac_address = mac_address
            voucher_session.status = "active"
            voucher_session.time_in = timezone.now()
            voucher_session.time_out = None
            voucher_session.save(
                update_fields=[
                    "mac_address",
                    "status",
                    "time_in",
                    "time_out",
                ]
            )

            dl_kbps = int(voucher_session.plan.speed_limit * 1024) if voucher_session.plan and voucher_session.plan.speed_limit else None
            ul_kbps = int(voucher_session.plan.speed_limit_upload * 1024) if voucher_session.plan and voucher_session.plan.speed_limit_upload else dl_kbps
            if not iptables.allow_device(mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps):
                raise RuntimeError("Failed to restore internet access for this device")
    except RuntimeError as exc:
        return Response(
            {"error": str(exc)},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    cache.delete(f"session-extend:{_client_ip(request)}:{mac_address}")
    audit_logger.info(
        "event=session_extend_success mode=new_session mac=%s voucher=%s ip=%s",
        mac_address,
        voucher_code,
        _client_ip(request),
    )

    return Response(
        {
            "status": "success",
            "message": "New session started from voucher",
            "session": SessionSerializer(voucher_session).data,
        }
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def session_extend_paid(request):
    """
    Extend an active session using the coin payment flow (no voucher code).
    POST /api/session/extend-paid/
    Body: { "mac_address": "...", "plan_id": ... }
    """
    mac_address = request.data.get("mac_address", "").upper().strip()
    plan_id = request.data.get("plan_id")

    from dashboard.models import SystemSettings
    from django.core.cache import cache
    
    settings_obj = SystemSettings.get_settings()
    if settings_obj.enable_internet_check:
        if cache.get("internet_status_ok") is False:
            return Response(
                {"error": "Internet Connection is Offline. Coin slot disabled."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    if not mac_address or not plan_id:
        return Response(
            {"error": "mac_address and plan_id are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        plan = Plan.objects.get(id=int(plan_id), is_active=True)
    except (Plan.DoesNotExist, ValueError, TypeError):
        return Response(
            {"error": "Invalid plan"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    request_ip = _client_ip(request)
    active_session = Session.objects.filter(
        mac_address=mac_address,
        status__in=["active", "paused"],
    ).select_related("plan").order_by("-id").first()

    if not active_session:
        active_session = Session.objects.filter(
            mac_address=mac_address,
        ).select_related("plan").order_by("-id").first()

    if not active_session:
        active_session = Session.objects.create(
            mac_address=mac_address,
            plan=plan,
            time_in=timezone.now(),
            duration_minutes_purchased=0,
            amount_paid=0,
            ip_address=request_ip,
            status="active",
        )

    if request_ip and request_ip != "127.0.0.1":
        active_session.ip_address = request_ip

    coin_req = CoinInsertRequest.objects.filter(
        mac_address__iexact=mac_address,
        status__in=[CoinInsertRequest.STATUS_PENDING, CoinInsertRequest.STATUS_ACTIVE],
    ).order_by("-id").first()

    is_group_pass = bool(request.data.get("is_group_pass") or (coin_req and coin_req.is_group_pass and coin_req.plan_id == plan.id))
    group_pass_devices = int(request.data.get("group_devices") or (coin_req.group_pass_devices if (coin_req and is_group_pass) else 1))
    if is_group_pass and group_pass_devices < 2:
        group_pass_devices = 2
    expected_amount = plan.price * group_pass_devices if is_group_pass else plan.price

    # Check coins
    total_coins = _pending_coin_events_for_mac(mac_address).aggregate(
        total=Sum("amount")
    )["total"] or 0

    if total_coins < expected_amount:
        try:
            coin_request, _ = _get_or_create_start_coin_request(
                mac_address, request_ip, plan,
                is_group_pass=is_group_pass,
                group_pass_devices=group_pass_devices
            )
        except ValueError as exc:
            return Response(
                {"error": str(exc), "required": expected_amount, "received": total_coins},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        return Response(
            {
                "error": f"Insufficient payment. Need {PESO_SYMBOL}{expected_amount}, received {PESO_SYMBOL}{total_coins}",
                "required": expected_amount,
                "received": total_coins,
                "coin_request": _coin_request_payload(coin_request),
            },
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )

    # Extend the session
    try:
        with transaction.atomic():
            multiplier = total_coins // expected_amount if expected_amount > 0 else 1
            amount_paid = expected_amount * multiplier
            duration_minutes = plan.duration_minutes * multiplier

            session_group = None
            if is_group_pass:
                import random
                import string
                while True:
                    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
                    if not SessionGroup.objects.filter(group_code=code).exists():
                        break

                expiry_hours = getattr(settings_obj, 'group_code_expiry_hours', 24)
                code_expires_at = None
                if expiry_hours > 0:
                    code_expires_at = timezone.now() + timedelta(hours=expiry_hours)

                session_group = SessionGroup.objects.create(
                    group_code=code,
                    plan=plan,
                    max_devices=group_pass_devices,
                    redeemed_count=1,
                    total_price=amount_paid,
                    duration_minutes=plan.duration_minutes,
                    time_in=timezone.now(),
                    code_expires_at=code_expires_at,
                    status="active"
                )
                active_session.session_group = session_group

            active_session.extend_session(duration_minutes)
            active_session.amount_paid = (active_session.amount_paid or 0) + amount_paid
            
            # Speed Always Wins logic
            speed_changed = False
            if active_session.plan:
                new_dl = plan.speed_limit or 0
                old_dl = active_session.plan.speed_limit or 0
                if new_dl > old_dl:
                    active_session.plan = plan
                    speed_changed = True
            else:
                active_session.plan = plan
                speed_changed = True

            update_fields = ["duration_minutes_purchased", "amount_paid", "status", "time_in"]
            if active_session.ip_address:
                update_fields.append("ip_address")
            if speed_changed:
                update_fields.append("plan")
            if is_group_pass and session_group:
                update_fields.append("session_group")

            # Additive Pause Replenishment (Zero Loophole: grants earned pauses of new plan)
            if plan.pause_limit == 0:
                if active_session.pause_count > 0:
                    active_session.pause_count = 0
                    update_fields.append("pause_count")
            elif plan.pause_limit > 0 and active_session.pause_count > 0:
                earned_pauses = plan.pause_limit * multiplier
                active_session.pause_count = max(0, active_session.pause_count - earned_pauses)
                update_fields.append("pause_count")
                
            active_session.save(update_fields=update_fields)
            
            PurchaseTransaction.objects.create(
                session=active_session,
                plan=plan,
                amount=amount_paid,
            )
            if is_group_pass and session_group and session_group.max_devices > 0:
                host_points_value = session_group.total_price / session_group.max_devices
                DeviceProfile.add_spending_points(mac_address, host_points_value)
            else:
                DeviceProfile.add_spending_points(mac_address, amount_paid)
            
            try:
                dl_kbps = int(plan.speed_limit * 1024) if plan.speed_limit else None
                ul_kbps = int(plan.speed_limit_upload * 1024) if plan.speed_limit_upload else dl_kbps

                if active_session.status == "active":
                    iptables.allow_device(mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps)
                elif speed_changed:
                    iptables.apply_bandwidth_limit(mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps)
            except Exception as e:
                audit_logger.warning("iptables_extend_warning mac=%s error=%s", mac_address, e)

            used_amount = 0
            for event in _pending_coin_events_for_mac(mac_address):
                if used_amount >= amount_paid:
                    break
                event.session = active_session
                event.save(update_fields=["session"])
                used_amount += event.amount

            # Return overpayment as balance
            overpayment = used_amount - amount_paid
            if overpayment > 0:
                CoinEvent.objects.create(
                    mac_address=mac_address,
                    amount=overpayment,
                    denomination=overpayment if overpayment in (1, 5, 10, 20) else 1,
                    session=None,
                )

            CoinInsertRequest.objects.filter(
                mac_address__iexact=mac_address,
                purpose=CoinInsertRequest.PURPOSE_START,
                status__in=[CoinInsertRequest.STATUS_PENDING, CoinInsertRequest.STATUS_ACTIVE, CoinInsertRequest.STATUS_COMPLETED],
            ).update(
                status=CoinInsertRequest.STATUS_COMPLETED,
                completed_at=timezone.now(),
            )
    except Exception as exc:
        audit_logger.error("event=session_extend_paid_error mac=%s error=%s", mac_address, exc)
        return Response(
            {"error": str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    _activate_next_coin_request()

    audit_logger.info(
        "event=session_extend_paid mac=%s plan=%s minutes=%s ip=%s",
        mac_address,
        plan.name,
        plan.duration_minutes,
        request_ip,
    )

    msg = f"Group Pass created! Code: {session_group.group_code} (+{plan.duration_display} added)" if session_group else f"Session extended by {plan.duration_minutes} minutes"

    return Response(
        {
            "status": "success",
            "message": msg,
            "session": SessionSerializer(active_session).data,
            "session_group": session_group.group_code if session_group else None,
        }
    )


@api_view(["POST"])
def session_end(request):
    """
    Ends session when time expires.
    POST /api/session/end/
    """
    auth_error = _require_dashboard_admin_response(request)
    if auth_error:
        return auth_error

    mac_address = request.data.get("mac_address", "").upper()
    session_id = request.data.get("session_id")

    if session_id:
        try:
            session = Session.objects.get(id=session_id, status="active")
        except Session.DoesNotExist:
            return Response(
                {"error": "Active session not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
    elif mac_address:
        session = Session.objects.filter(
            mac_address=mac_address,
            status="active",
        ).first()
        if not session:
            return Response(
                {"error": "No active session for this device"},
                status=status.HTTP_404_NOT_FOUND,
            )
    else:
        return Response(
            {"error": "Provide mac_address or session_id"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    session.expire_session()
    blocked = iptables.block_device(session.mac_address)
    audit_logger.info(
        "event=session_end user=%s target_mac=%s access_revoked=%s ip=%s",
        request.user.username,
        session.mac_address,
        blocked,
        _client_ip(request),
    )

    return Response(
        {
            "status": "success",
            "message": "Session ended",
            "session": SessionSerializer(session).data,
            "access_revoked": blocked,
        }
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def session_pause_toggle(request):
    """
    Toggle pause/resume for an active session.
    POST /api/session/pause/
    """
    mac_address = request.data.get("mac_address", "").upper()
    if not mac_address:
        return Response(
            {"error": "mac_address is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    session = Session.objects.filter(
        mac_address=mac_address,
        status__in=["active", "paused"],
    ).first()

    if not session:
        return Response(
            {"error": "No active or paused session found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    _session_ip_matches_request(session, request)

    if session.status == "active":
        if session.plan.pause_limit > 0 and session.pause_count >= session.plan.pause_limit:
            return Response(
                {"error": f"You have reached the maximum number of pauses ({session.plan.pause_limit}) for this plan."},
                status=status.HTTP_403_FORBIDDEN,
            )

        session.pause_session()
        blocked = iptables.block_device(mac_address)
        audit_logger.info(
            "event=session_paused mac=%s blocked=%s ip=%s",
            mac_address, blocked, _client_ip(request),
        )
        return Response({
            "status": "paused",
            "message": "Session paused. Internet disconnected.",
            "time_remaining_seconds": session.time_remaining_seconds,
        })
    else:
        # Check if max pause hours exceeded
        from dashboard.models import SystemSettings
        sys_settings = SystemSettings.get_settings()
        max_pause_hours = 0
        if session.plan and session.plan.pause_duration_limit > 0:
            max_pause_hours = session.plan.pause_duration_limit
        elif sys_settings and sys_settings.global_pause_limit_hours > 0:
            max_pause_hours = sys_settings.global_pause_limit_hours

        if max_pause_hours > 0 and session.paused_at:
            paused_hours = (timezone.now() - session.paused_at).total_seconds() / 3600.0
            if paused_hours >= max_pause_hours:
                session.expire_session()
                return Response(
                    {"error": f"Session has expired. Paused duration exceeded maximum limit of {max_pause_hours} hours."},
                    status=status.HTTP_410_GONE,
                )

        # Check if network is full before allowing resume
        max_sessions = getattr(settings, "PISONET_MAX_CONCURRENT_SESSIONS", 20)
        active_count = Session.objects.filter(status="active").count()
        if active_count >= max_sessions:
            return Response(
                {"error": f"Network is full ({max_sessions} active users). Please try resuming later."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        session.resume_session()
        dl_kbps = int(session.plan.speed_limit * 1024) if session.plan and session.plan.speed_limit else None
        ul_kbps = int(session.plan.speed_limit_upload * 1024) if session.plan and session.plan.speed_limit_upload else dl_kbps
        allowed = iptables.allow_device(mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps)
        audit_logger.info(
            "event=session_resumed mac=%s allowed=%s ip=%s",
            mac_address, allowed, _client_ip(request),
        )
        return Response({
            "status": "active",
            "message": "Session resumed. Internet reconnected.",
            "time_remaining_seconds": session.time_remaining_seconds,
        })


@api_view(["GET"])
@permission_classes([AllowAny])
def session_status(request):
    """
    Returns remaining time for active session.
    GET /api/session/status/?mac_address=AA:BB:CC:DD:EE:FF
    """
    mac_address = request.query_params.get("mac_address", "").upper()

    if not mac_address:
        return Response(
            {"error": "mac_address parameter required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if _public_read_rate_limited(request, "session-status"):
        audit_logger.warning("event=session_status_rate_limited ip=%s", _client_ip(request))
        return Response(
            {"error": "Too many requests. Please retry shortly."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    is_whitelisted = WhitelistedDevice.objects.filter(
        mac_address=mac_address
    ).exists()
    if is_whitelisted:
        return Response(
            {
                "status": "whitelisted",
                "message": "Device is whitelisted - unlimited access",
                "mac_address": mac_address,
                "is_whitelisted": True,
            }
        )

    from dashboard.models import Announcement, SystemSettings

    session = Session.objects.filter(
        mac_address=mac_address,
        status__in=["active", "paused"],
    ).first()

    active_ann = Announcement.objects.filter(is_active=True).first()
    ann_text = active_ann.message if active_ann else None
    isp_outage = Announcement.objects.filter(is_active=True, message__contains="interrupted by our ISP").exists()

    if session:
        if session.status == "paused":
            # Check if paused session exceeded global max pause hours
            settings_obj = SystemSettings.get_settings()
            max_pause_hours = settings_obj.global_pause_limit_hours if settings_obj else 24
            if max_pause_hours > 0 and session.paused_at:
                pause_age_hours = (timezone.now() - session.paused_at).total_seconds() / 3600.0
                if pause_age_hours >= max_pause_hours:
                    session.expire_session()
                    blocked = iptables.block_device(session.mac_address)
                    return Response(
                        {
                            "status": "expired",
                            "message": f"Paused session expired after exceeding {max_pause_hours}h limit",
                            "session": SessionSerializer(session).data,
                            "access_revoked": blocked,
                        }
                    )

            return Response(
                {
                    "status": "paused",
                    "message": "Session is paused",
                    "session": SessionSerializer(session).data,
                    "is_whitelisted": False,
                    "isp_outage": isp_outage,
                    "announcement": ann_text,
                }
            )

        with transaction.atomic():
            locked_session = Session.objects.select_for_update().filter(
                id=session.id,
                status="active",
            ).first()

            # Another request may have already expired this session.
            if locked_session is None:
                return Response(
                    {
                        "status": "expired",
                        "message": "Session has expired",
                        "session": SessionSerializer(session).data,
                        "access_revoked": False,
                    }
                )

            _session_ip_matches_request(locked_session, request)

            if locked_session.device_name in [None, "", "Unknown"]:
                detected = _extract_device_name(request, mac_address=locked_session.mac_address)
                if detected and detected != "Unknown":
                    locked_session.device_name = detected
                    locked_session.save(update_fields=["device_name"])

            if locked_session.time_remaining_seconds <= 0:
                locked_session.expire_session()
                blocked = iptables.block_device(locked_session.mac_address)
                return Response(
                    {
                        "status": "expired",
                        "message": "Session has expired",
                        "session": SessionSerializer(locked_session).data,
                        "access_revoked": blocked,
                    }
                )

            refresh_session_bandwidth_usage(locked_session)
            response_data = {
                "status": "active",
                "session": SessionSerializer(locked_session).data,
                "is_whitelisted": False,
                "isp_outage": isp_outage,
                "announcement": ann_text,
            }
            if locked_session.session_group:
                grp = locked_session.session_group
                response_data["group_redeemed"] = grp.redeemed_count
                response_data["group_max"] = grp.max_devices
                response_data["group_code"] = grp.group_code
                response_data["group_code_expires_at"] = (
                    grp.code_expires_at.isoformat() if grp.code_expires_at else None
                )
            return Response(response_data)

    return Response(
        {
            "status": "no_session",
            "message": "No active session found",
            "mac_address": mac_address,
            "is_whitelisted": False,
        }
    )


@api_view(["GET"])
def connected_users(request):
    """
    Returns list of currently connected devices.
    GET /api/connected-users/
    """
    auth_error = _require_dashboard_admin_response(request)
    if auth_error:
        return auth_error

    active_sessions = Session.objects.filter(status="active").select_related("plan")
    whitelisted = WhitelistedDevice.objects.all()

    return Response(
        {
            "active_sessions": SessionSerializer(active_sessions, many=True).data,
            "whitelisted_devices": WhitelistedDeviceSerializer(whitelisted, many=True).data,
            "total_connected": active_sessions.count() + whitelisted.count(),
        }
    )


@api_view(["GET"])
def bandwidth_usage(request):
    """
    Returns real bandwidth usage per user from iptables byte counters.
    GET /api/bandwidth/
    """
    auth_error = _require_dashboard_admin_response(request)
    if auth_error:
        return auth_error

    from .bandwidth import get_all_device_bandwidth_mb

    # Get real bandwidth from iptables
    device_bandwidth = get_all_device_bandwidth_mb()

    # Match MACs with active sessions for device names
    active_sessions = {
        s.mac_address.upper(): s.device_name or 'Unknown'
        for s in Session.objects.filter(status="active")
    }

    users = []
    total_bandwidth = 0.0
    for entry in device_bandwidth:
        mac = entry['mac_address']
        mb = entry['bandwidth_mb']
        total_bandwidth += mb
        users.append({
            'mac_address': mac,
            'device_name': active_sessions.get(mac, 'Unknown'),
            'bandwidth_used_mb': mb,
        })

    return Response({
        "users": users,
        "total_bandwidth_mb": round(total_bandwidth, 2),
    })


@api_view(["POST"])
def whitelist_device(request):
    """
    Adds device to whitelist.
    POST /api/whitelist/
    """
    auth_error = _require_dashboard_admin_response(request)
    if auth_error:
        return auth_error

    serializer = WhitelistedDeviceSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    mac_address = serializer.validated_data["mac_address"]
    if WhitelistedDevice.objects.filter(mac_address=mac_address).exists():
        return Response(
            {"error": "Device already whitelisted"},
            status=status.HTTP_409_CONFLICT,
        )

    try:
        with transaction.atomic():
            device = serializer.save(mac_address=mac_address)
            if not iptables.whitelist_device(mac_address):
                raise RuntimeError("Failed to apply whitelist rule for this device")
    except RuntimeError as exc:
        return Response(
            {"error": str(exc)},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    audit_logger.info(
        "event=whitelist_add user=%s mac=%s device_name=%s ip=%s",
        request.user.username,
        device.mac_address,
        device.device_name,
        _client_ip(request),
    )

    return Response(
        {
            "status": "success",
            "message": f"{device.device_name} added to whitelist",
            "device": WhitelistedDeviceSerializer(device).data,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def signal_strength(request):
    """
    Returns RSSI for connected devices.
    GET /api/signal-strength/
    Reads real data from Linux iw/iwinfo command when available.
    """
    if _public_read_rate_limited(request, "signal-strength"):
        audit_logger.warning("event=signal_strength_rate_limited ip=%s", _client_ip(request))
        return Response(
            {"error": "Too many requests. Please retry shortly."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    devices = []
    try:
        result = subprocess.run(
            ["iw", "dev", "wlan0", "station", "dump"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            current_mac = None
            current_rssi = None
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("Station "):
                    if current_mac and current_rssi is not None:
                        quality = (
                            "Excellent" if current_rssi > -50
                            else "Good" if current_rssi > -60
                            else "Fair" if current_rssi > -70
                            else "Poor"
                        )
                        band = "5GHz" if current_rssi > -50 else "2.4GHz"
                        session = Session.objects.filter(
                            mac_address=current_mac.upper(), status="active"
                        ).first()
                        devices.append({
                            "mac_address": current_mac.upper(),
                            "device_name": (session.device_name if session else None) or "Unknown",
                            "rssi": current_rssi,
                            "signal_quality": quality,
                            "recommended_band": band,
                        })
                    parts = line.split()
                    current_mac = parts[1] if len(parts) > 1 else None
                    current_rssi = None
                elif "signal:" in line or "signal avg:" in line:
                    try:
                        current_rssi = int(line.split(":")[1].strip().split()[0])
                    except (IndexError, ValueError):
                        pass
            # Final station
            if current_mac and current_rssi is not None:
                quality = (
                    "Excellent" if current_rssi > -50
                    else "Good" if current_rssi > -60
                    else "Fair" if current_rssi > -70
                    else "Poor"
                )
                band = "5GHz" if current_rssi > -50 else "2.4GHz"
                session = Session.objects.filter(
                    mac_address=current_mac.upper(), status="active"
                ).first()
                devices.append({
                    "mac_address": current_mac.upper(),
                    "device_name": (session.device_name if session else None) or "Unknown",
                    "rssi": current_rssi,
                    "signal_quality": quality,
                    "recommended_band": band,
                })
    except FileNotFoundError:
        logger.info("iw command not available — signal strength data unavailable")
    except Exception as exc:
        logger.warning("signal_strength read error: %s", exc)

    # Anonymize MAC addresses for non-admin users
    is_admin = request.user and request.user.is_authenticated and request.user.is_staff
    if not is_admin:
        for device in devices:
            mac = device.get("mac_address", "")
            if len(mac) == 17:
                device["mac_address"] = mac[:8] + ":XX:XX:XX"

    return Response({"devices": devices})


@api_view(["GET"])
@permission_classes([AllowAny])
def speed_test(request):
    """
    Returns internet speed estimate for a specific device.
    GET /api/speed-test/?mac_address=AA:BB:CC:DD:EE:FF
    """
    mac_address = request.query_params.get("mac_address", "").upper()

    if not mac_address:
        return Response(
            {"error": "mac_address parameter required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if _public_read_rate_limited(request, "speed-test"):
        audit_logger.warning("event=speed_test_rate_limited ip=%s", _client_ip(request))
        return Response(
            {"error": "Too many requests. Please retry shortly."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    session = Session.objects.filter(
        mac_address=mac_address,
        status="active",
    ).select_related("plan").first()

    if session and not _session_ip_matches_request(session, request):
        request_ip = _client_ip(request)
        SuspiciousDevice.record_incident(
            mac_address=mac_address,
            ip_address=request_ip,
            reason="mac_ip_conflict_speed_test",
            evidence=f"Speed-test request IP {request_ip} differs from active session IP {session.ip_address}",
        )
        audit_logger.warning(
            "event=speed_test_ip_mismatch mac=%s request_ip=%s session_ip=%s",
            mac_address,
            request_ip,
            session.ip_address,
        )
        return Response(
            {"error": "No active session found for this device"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not session:
        return Response(
            {"error": "No active session found for this device"},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Estimated speed based on plan cap
    cap = float(session.plan.speed_limit) if session.plan.speed_limit else 30.0
    download = round(cap * 0.85, 2)
    upload = round(max(0.5, cap * 0.35), 2)
    ping = 18

    return Response(
        {
            "mac_address": mac_address,
            "download_mbps": download,
            "upload_mbps": upload,
            "ping_ms": ping,
            "speed_mode": "estimated",
            "mode_label": "Estimated from plan speed cap",
            "measured_at": timezone.now(),
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def plans_list(request):
    """
    Returns list of active plans.
    GET /api/plans/
    """
    if _public_read_rate_limited(request, "plans-list"):
        audit_logger.warning("event=plans_list_rate_limited ip=%s", _client_ip(request))
        return Response(
            {"error": "Too many requests. Please retry shortly."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    plans = Plan.objects.filter(is_active=True)
    return Response({"plans": PlanSerializer(plans, many=True).data})



from django.http import StreamingHttpResponse
import os

@api_view(["GET"])
@permission_classes([AllowAny])
def speed_test_download(request):
    """
    Endpoint for performing a real download speed test.
    Streams 10MB of random data.
    """
    chunk_size = 65536
    chunks = (10 * 1024 * 1024) // chunk_size

    def stream_random_data():
        for _ in range(chunks):
            yield os.urandom(chunk_size)

    response = StreamingHttpResponse(stream_random_data(), content_type="application/octet-stream")
    response['Content-Length'] = str(10 * 1024 * 1024)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

@api_view(["POST"])
@permission_classes([AllowAny])
def speed_test_upload(request):
    """
    Endpoint for performing a real upload speed test.
    Accepts arbitrary data and returns 200 OK.
    """
    return Response({"status": "success", "message": "Upload test completed"}, status=status.HTTP_200_OK)
