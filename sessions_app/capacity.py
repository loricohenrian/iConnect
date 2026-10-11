"""Shared admission checks for paid sessions and administrator actions."""
from contextlib import contextmanager
from functools import wraps

from django.db import transaction
from rest_framework.response import Response

from .models import Session

NETWORK_FULL_MESSAGE = "Network full—please wait."


def network_full_for_device(mac_address, settings_obj=None):
    """An already-active device can extend without taking another slot."""
    if Session.objects.filter(mac_address__iexact=mac_address or "", status="active").exists():
        return False
    if settings_obj is None:
        from dashboard.models import SystemSettings
        settings_obj = SystemSettings.get_settings()
    return Session.objects.filter(status="active").count() >= settings_obj.max_concurrent_sessions


@contextmanager
def session_capacity_lock():
    """Serialize web admissions on the existing singleton row; no new schema."""
    from dashboard.models import SystemSettings
    SystemSettings.get_settings()
    with transaction.atomic():
        settings_obj = SystemSettings.objects.select_for_update().get(pk=1)
        yield settings_obj


def capacity_limited_activation(view):
    """Check capacity and keep the lock until the activation is committed."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        with session_capacity_lock() as settings_obj:
            mac = request.data.get("mac_address", "")
            if mac and network_full_for_device(mac, settings_obj):
                return Response({"error": NETWORK_FULL_MESSAGE, "network_full": True}, status=503)
            return view(request, *args, **kwargs)
    return wrapped


def resume_admin_session(session_id):
    """Resume one eligible session without exceeding capacity or losing paid time."""
    from django.utils import timezone
    from . import iptables
    from .models import SuspiciousDevice
    granted = False
    current = None
    try:
        with session_capacity_lock() as settings_obj:
            current = Session.objects.select_for_update(of=("self",)).select_related("plan").get(pk=session_id)
            if current.status != "paused":
                return "not_paused"
            if SuspiciousDevice.objects.filter(mac_address__iexact=current.mac_address, is_blocked=True).exists():
                return "blocked"
            if current.time_remaining_seconds <= 0:
                current.status = "expired"
                current.time_out = timezone.now()
                current.paused_at = None
                current.save(update_fields=["status", "time_out", "paused_at"])
                return "expired"
            if Session.objects.filter(status="active").count() >= settings_obj.max_concurrent_sessions:
                return "full"
            if current.power_paused:
                from .power_recovery import resume_power_session
                return "resumed" if resume_power_session(current) else "failed"
            dl = int(current.plan.speed_limit * 1024) if current.plan and current.plan.speed_limit else None
            ul = int(current.plan.speed_limit_upload * 1024) if current.plan and current.plan.speed_limit_upload else dl
            if not iptables.allow_device(current.mac_address, rate_kbps=dl, upload_kbps=ul):
                iptables.block_device(current.mac_address)
                return "failed"
            granted = True
            if not current.resume_session():
                iptables.block_device(current.mac_address)
                return "failed"
            return "resumed"
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Could not resume session %s", session_id)
        if granted and current:
            with session_capacity_lock():
                pending = Session.objects.select_for_update().get(pk=session_id)
                if pending.status == "paused":
                    iptables.block_device(pending.mac_address)
        return "failed"
