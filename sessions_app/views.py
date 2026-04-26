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
from .models import CoinEvent, CoinInsertRequest, Plan, Session, WhitelistedDevice, SuspiciousDevice
from .serializers import (
    CoinInsertedSerializer,
    PlanSerializer,
    SessionExtendSerializer,
    SessionSerializer,
    SessionStartSerializer,
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
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
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
    if not session.ip_address:
        return False
    return session.ip_address == _client_ip(request)


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
    return CoinEvent.objects.filter(
        session__isnull=True,
        mac_address=mac_address,
    ).order_by("timestamp", "id")


def _ensure_firewall_ready_for_session_start():
    """Ensure baseline firewall policy is enforced before starting a session."""
    if not getattr(settings, "PISONET_REQUIRE_FORWARD_DROP_BEFORE_SESSION", True):
        return True
    return iptables.enforce_firewall_baseline()


def _coin_request_window_seconds():
    return max(15, int(getattr(settings, "PISONET_COIN_REQUEST_WINDOW_SECONDS", 45)))


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
        "ready_to_start": coin_request.status == CoinInsertRequest.STATUS_COMPLETED,
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
    elif (
        coin_request.status in (CoinInsertRequest.STATUS_PENDING, CoinInsertRequest.STATUS_ACTIVE)
        and coin_request.expected_amount > 0
        and credited_amount >= coin_request.expected_amount
    ):
        coin_request.status = CoinInsertRequest.STATUS_COMPLETED
        coin_request.completed_at = now
        update_fields.extend(["status", "completed_at"])
        transitioned = True

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


def _get_or_create_start_coin_request(mac_address, ip_address, plan):
    """Create/reuse a queued start-session coin request for this device."""
    existing_request = CoinInsertRequest.objects.filter(
        mac_address=mac_address,
        purpose=CoinInsertRequest.PURPOSE_START,
        status__in=[CoinInsertRequest.STATUS_PENDING, CoinInsertRequest.STATUS_ACTIVE],
    ).order_by("created_at", "id").first()

    if existing_request:
        return _sync_coin_request_progress(existing_request), False

    queue_depth = CoinInsertRequest.objects.filter(
        status__in=[CoinInsertRequest.STATUS_PENDING, CoinInsertRequest.STATUS_ACTIVE]
    ).count()
    if queue_depth >= _coin_request_max_queue():
        raise ValueError("Coin request queue is full. Please try again shortly.")

    credited_amount = _pending_coin_events_for_mac(mac_address).aggregate(total=Sum("amount"))["total"] or 0
    initial_status = (
        CoinInsertRequest.STATUS_COMPLETED
        if credited_amount >= plan.price
        else CoinInsertRequest.STATUS_PENDING
    )

    coin_request = CoinInsertRequest.objects.create(
        mac_address=mac_address,
        ip_address=ip_address,
        purpose=CoinInsertRequest.PURPOSE_START,
        plan=plan,
        expected_amount=plan.price,
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
    assigned_request = None

    if not mac_address:
        assigned_request = _active_coin_request_for_unscoped_insert()
        if assigned_request:
            mac_address = assigned_request.mac_address

    session = None
    voucher_code = None
    if mac_address:
        session = Session.objects.filter(
            mac_address=mac_address,
            status="active",
        ).first()

    coin_event = CoinEvent.objects.create(
        amount=amount,
        denomination=denomination,
        mac_address=mac_address,
        session=session,
    )
    audit_logger.info(
        "event=coin_received amount=%s denomination=%s mac=%s request_id=%s ip=%s",
        amount,
        denomination,
        mac_address or "<none>",
        assigned_request.id if assigned_request else "<none>",
        _client_ip(request),
    )

    if assigned_request:
        assigned_request = _sync_coin_request_progress(assigned_request)

    if session:
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

    return Response(
        {
            "status": "success",
            "message": f"{PESO_SYMBOL}{amount} coin received",
            "coin_event_id": coin_event.id,
            "voucher_code": voucher_code,
            "amount": amount,
            "assigned_mac_address": mac_address,
            "coin_request": _coin_request_payload(assigned_request),
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def session_start_request(request):
    """Create/retrieve a queued coin-insert request for starting a session."""
    serializer = SessionStartSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    mac_address = serializer.validated_data["mac_address"]
    plan_id = serializer.validated_data["plan_id"]
    ip_address = _client_ip(request)

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
        coin_request, created = _get_or_create_start_coin_request(mac_address, ip_address, plan)
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
def session_start(request):
    """
    Creates new session after payment.
    POST /api/session/start/
    """
    serializer = SessionStartSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    mac_address = serializer.validated_data["mac_address"]
    plan_id = serializer.validated_data["plan_id"]
    ip_address = _client_ip(request)
    device_name = serializer.validated_data.get("device_name")

    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response(
            {"error": "Plan not found or inactive"},
            status=status.HTTP_404_NOT_FOUND,
        )

    existing = Session.objects.filter(
        mac_address=mac_address,
        status="active",
    ).first()
    if existing:
        if existing.ip_address and existing.ip_address != ip_address:
            SuspiciousDevice.record_incident(
                mac_address=mac_address,
                ip_address=ip_address,
                reason="mac_ip_conflict_start",
                evidence=f"Active session already bound to IP {existing.ip_address}",
            )
            audit_logger.warning(
                "event=session_start_mac_clone_detected mac=%s existing_ip=%s request_ip=%s",
                mac_address,
                existing.ip_address,
                ip_address,
            )
            return Response(
                {
                    "error": "Possible MAC cloning detected. Active session is bound to another IP.",
                    "session": SessionSerializer(existing).data,
                    "suspected_clone": True,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response(
            {
                "error": "Device already has an active session",
                "session": SessionSerializer(existing).data,
            },
            status=status.HTTP_409_CONFLICT,
        )

    if not _ensure_firewall_ready_for_session_start():
        audit_logger.error(
            "event=session_start_firewall_baseline_failed mac=%s ip=%s",
            mac_address,
            ip_address,
        )
        return Response(
            {"error": "Firewall baseline is not ready. Please retry shortly."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    total_coins = _pending_coin_events_for_mac(mac_address).aggregate(
        total=Sum("amount")
    )["total"] or 0

    if total_coins < plan.price:
        try:
            coin_request, _ = _get_or_create_start_coin_request(mac_address, ip_address, plan)
        except ValueError as exc:
            return Response(
                {
                    "error": str(exc),
                    "required": plan.price,
                    "received": total_coins,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        return Response(
            {
                "error": (
                    f"Insufficient payment for {mac_address}. "
                    f"Need {PESO_SYMBOL}{plan.price}, received {PESO_SYMBOL}{total_coins}"
                ),
                "required": plan.price,
                "received": total_coins,
                "coin_request": _coin_request_payload(coin_request),
            },
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )

    try:
        with transaction.atomic():
            session = Session.objects.create(
                mac_address=mac_address,
                plan=plan,
                time_in=timezone.now(),
                duration_minutes_purchased=plan.duration_minutes,
                amount_paid=plan.price,
                ip_address=ip_address,
                device_name=device_name,
                status="active",
            )

            used_amount = 0
            for event in _pending_coin_events_for_mac(mac_address):
                if used_amount >= plan.price:
                    break
                event.session = session
                event.save(update_fields=["session"])
                used_amount += event.amount

            CoinInsertRequest.objects.filter(
                mac_address=mac_address,
                purpose=CoinInsertRequest.PURPOSE_START,
                status__in=[CoinInsertRequest.STATUS_PENDING, CoinInsertRequest.STATUS_ACTIVE],
            ).update(
                status=CoinInsertRequest.STATUS_CANCELLED,
                completed_at=timezone.now(),
            )

            if not iptables.allow_device(mac_address):
                raise RuntimeError("Failed to allow internet access for this device")
    except RuntimeError as exc:
        return Response(
            {"error": str(exc)},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    _activate_next_coin_request()

    return Response(
        {
            "status": "success",
            "message": "Session started",
            "session": SessionSerializer(session).data,
        },
        status=status.HTTP_201_CREATED,
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

    if active_session and not _session_ip_matches_request(active_session, request):
        request_ip = _client_ip(request)
        SuspiciousDevice.record_incident(
            mac_address=mac_address,
            ip_address=request_ip,
            reason="mac_ip_conflict_extend",
            evidence=f"Session extend request IP {request_ip} differs from active session IP {active_session.ip_address}",
        )
        audit_logger.warning(
            "event=session_extend_ip_mismatch mac=%s request_ip=%s session_ip=%s",
            mac_address,
            request_ip,
            active_session.ip_address,
        )
        return Response(
            {
                "error": "Session is active on a different IP. Possible MAC cloning detected.",
                "suspected_clone": True,
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    if active_session:
        active_session.extend_session(voucher_session.duration_minutes_purchased)
        active_session.amount_paid += voucher_session.amount_paid
        active_session.save(update_fields=["duration_minutes_purchased", "amount_paid", "status", "time_in"])

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

            if not iptables.allow_device(mac_address):
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
    mac_address = request.data.get("mac_address", "").upper()
    plan_id = request.data.get("plan_id")

    if not mac_address or not plan_id:
        return Response(
            {"error": "mac_address and plan_id are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response(
            {"error": "Invalid plan"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    request_ip = _client_ip(request)
    active_session = Session.objects.filter(
        mac_address=mac_address,
        status="active",
        ip_address=request_ip,
    ).select_related("plan").first()

    if not active_session:
        return Response(
            {"error": "No active session found for this device"},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Check coins
    total_coins = _pending_coin_events_for_mac(mac_address).aggregate(
        total=Sum("amount")
    )["total"] or 0

    if total_coins < plan.price:
        try:
            coin_request, _ = _get_or_create_start_coin_request(mac_address, request_ip, plan)
        except ValueError as exc:
            return Response(
                {"error": str(exc), "required": plan.price, "received": total_coins},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        return Response(
            {
                "error": f"Insufficient payment. Need {PESO_SYMBOL}{plan.price}, received {PESO_SYMBOL}{total_coins}",
                "required": plan.price,
                "received": total_coins,
                "coin_request": _coin_request_payload(coin_request),
            },
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )

    # Extend the session
    try:
        with transaction.atomic():
            active_session.extend_session(plan.duration_minutes)
            active_session.amount_paid += plan.price
            active_session.save(update_fields=["duration_minutes_purchased", "amount_paid", "status", "time_in"])

            used_amount = 0
            for event in _pending_coin_events_for_mac(mac_address):
                if used_amount >= plan.price:
                    break
                event.session = active_session
                event.save(update_fields=["session"])
                used_amount += event.amount

            CoinInsertRequest.objects.filter(
                mac_address=mac_address,
                purpose=CoinInsertRequest.PURPOSE_START,
                status__in=[CoinInsertRequest.STATUS_PENDING, CoinInsertRequest.STATUS_ACTIVE],
            ).update(
                status=CoinInsertRequest.STATUS_CANCELLED,
                completed_at=timezone.now(),
            )
    except Exception as exc:
        return Response(
            {"error": str(exc)},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    _activate_next_coin_request()

    audit_logger.info(
        "event=session_extend_paid mac=%s plan=%s minutes=%s ip=%s",
        mac_address,
        plan.name,
        plan.duration_minutes,
        request_ip,
    )

    return Response(
        {
            "status": "success",
            "message": f"Session extended by {plan.duration_minutes} minutes",
            "session": SessionSerializer(active_session).data,
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

    session = Session.objects.filter(
        mac_address=mac_address,
        status="active",
    ).first()

    if session:
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

            if not _session_ip_matches_request(locked_session, request):
                request_ip = _client_ip(request)
                SuspiciousDevice.record_incident(
                    mac_address=mac_address,
                    ip_address=request_ip,
                    reason="mac_ip_conflict_status",
                    evidence=f"Session status request IP {request_ip} differs from active session IP {locked_session.ip_address}",
                )
                audit_logger.warning(
                    "event=session_status_ip_mismatch mac=%s request_ip=%s session_ip=%s",
                    mac_address,
                    request_ip,
                    locked_session.ip_address,
                )
                return Response(
                    {
                        "status": "no_session",
                        "message": "No active session found",
                        "mac_address": mac_address,
                        "is_whitelisted": False,
                        "suspected_clone": True,
                    }
                )

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
            return Response(
                {
                    "status": "active",
                    "session": SessionSerializer(locked_session).data,
                    "is_whitelisted": False,
                }
            )

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
    Returns bandwidth usage per user.
    GET /api/bandwidth/
    """
    auth_error = _require_dashboard_admin_response(request)
    if auth_error:
        return auth_error

    active_sessions = Session.objects.filter(status="active").values(
        "mac_address",
        "device_name",
        "bandwidth_used_mb",
    )
    active_sessions_list = list(active_sessions)
    return Response(
        {
            "users": active_sessions_list,
            "total_bandwidth_mb": sum(
                session["bandwidth_used_mb"] for session in active_sessions_list
            ),
        }
    )


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


