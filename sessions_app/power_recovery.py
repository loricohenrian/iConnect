"""Power-loss recovery independent of Redis, worker lifetime and user pause limits."""
import logging
import math
import os
from datetime import datetime, timedelta, timezone as datetime_timezone
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Session, SessionPowerState

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def current_boot_id():
    if os.name == "nt":
        # Windows is a development host; production power recovery targets Linux/Pi.
        return "windows-development"
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _legacy_checkpoint(now):
    """One-time bridge for installations upgrading from heartbeat.txt."""
    try:
        stamp = float((Path(settings.BASE_DIR) / "heartbeat.txt").read_text().strip())
        if not math.isfinite(stamp) or stamp <= 0:
            return None
        return datetime.fromtimestamp(stamp, tz=datetime_timezone.utc)
    except (OSError, ValueError, OverflowError):
        return None


def _lock_state():
    state, created = SessionPowerState.objects.get_or_create(pk=1)
    # An UPDATE acquires the write lock on SQLite too, where select_for_update
    # alone is a no-op. Recovery and checkpoint updates serialize on this row.
    SessionPowerState.objects.filter(pk=1).update(boot_id=F("boot_id"))
    state.refresh_from_db()
    return state, created


def ensure_power_recovery():
    """Recover once per OS boot, before any HTTP view or expiry operation.

    Kernel boot IDs distinguish real shutdowns from ordinary worker restarts.
    Transactions make recovery idempotent across simultaneous web/workers.
    """
    boot_id = current_boot_id()
    if SessionPowerState.objects.filter(pk=1, boot_id=boot_id).exists():
        return 0
    recovered = []
    to_block = []
    with transaction.atomic():
        state, created = _lock_state()
        if state.boot_id == boot_id:
            return 0
        now = timezone.now()
        legacy = _legacy_checkpoint(now) if created else None
        # First deployment without a usable old heartbeat establishes a baseline;
        # it cannot invent balances for time already lost before this feature.
        reference = legacy if created else state.checkpoint_at
        initial = created and legacy is None
        if not initial and reference > now:
            raise RuntimeError("Clock is behind the saved power checkpoint; waiting for clock synchronization")
        for session in Session.objects.select_for_update().filter(status__in=("active", "paused")):
            if initial:
                session.power_checkpoint_at = now
                session.power_remaining_seconds = session._balance_at(now)
                Session.objects.filter(pk=session.pk).update(
                    power_checkpoint_at=now, power_remaining_seconds=session.power_remaining_seconds)
                continue
            checkpoint = session.power_checkpoint_at or reference
            if checkpoint > now:
                raise RuntimeError("Clock is behind a session checkpoint; waiting for clock synchronization")
            offline = max(0, (now - max(checkpoint, reference)).total_seconds())
            if session.power_paused:
                # An earlier recovery already froze this session. Leave its balance
                # and paused_at untouched, including through repeated multi-day boots.
                continue
            if session.status == "active":
                remaining = session.power_remaining_seconds
                if remaining is None:
                    remaining = session._balance_at(checkpoint)
                remaining = max(0, min(remaining, session.duration_minutes_purchased * 60))
                if remaining <= 0:
                    session.expire_session()
                    to_block.append(session.mac_address)
                    continue
                session.total_paused_seconds = (
                    (now - session.time_in).total_seconds()
                    - (session.duration_minutes_purchased * 60 - remaining))
                session.status = "paused"
                session.paused_at = now
                session.power_paused = True
                session.power_credited_seconds += max(0, (now - checkpoint).total_seconds())
                session.save(update_fields=["status", "paused_at", "total_paused_seconds",
                                           "power_paused", "power_credited_seconds"])
                recovered.append(session.mac_address)
                to_block.append(session.mac_address)
            elif session.paused_at:
                # Preserve manual/disconnect/ISP pauses as such; exclude only the
                # power-off interval from their ordinary pause and lifetime limits.
                session.paused_at += timedelta(seconds=offline)
                session.total_paused_seconds += offline
                session.power_credited_seconds += offline
                session.save(update_fields=["paused_at", "total_paused_seconds", "power_credited_seconds"])
        # Block before publishing the new boot marker. Otherwise another process
        # could resume a phone after commit, only for a delayed block to revoke it.
        _block_recovered(to_block)
        state.boot_id = boot_id
        state.checkpoint_at = now
        state.save(update_fields=["boot_id", "checkpoint_at"])
    if recovered:
        logger.info("Power recovery: preserved and paused %s sessions", len(recovered))
    return len(recovered)


def _block_recovered(macs):
    from . import iptables
    for mac in macs:
        try:
            iptables.block_device(mac)
        except Exception:
            logger.exception("Could not block power-paused device %s", mac)


def checkpoint_sessions():
    """Atomically store balances before expiry; never overwrite a newer purchase."""
    ensure_power_recovery()
    with transaction.atomic():
        state, _ = _lock_state()
        now = timezone.now()
        for session in Session.objects.filter(status__in=("active", "paused")):
            Session.objects.filter(pk=session.pk, status=session.status,
                                   power_checkpoint_at=session.power_checkpoint_at).update(
                power_checkpoint_at=now, power_remaining_seconds=session._balance_at(now))
        state.checkpoint_at = now
        state.save(update_fields=["checkpoint_at"])


def resume_power_session(session):
    """Start paid time only after firewall access succeeds; serialize competitors."""
    from . import iptables
    granted = False
    try:
        with transaction.atomic():
            locked = Session.objects.filter(pk=session.pk, status="paused", power_paused=True)
            # Explicit write lock also serializes this transition on SQLite.
            if not locked.update(power_paused=True):
                session.refresh_from_db()
                return session.status == "active"
            current = Session.objects.select_for_update().get(pk=session.pk)
            if current.time_remaining_seconds <= 0:
                return False
            dl = int(current.plan.speed_limit * 1024) if current.plan and current.plan.speed_limit else None
            ul = int(current.plan.speed_limit_upload * 1024) if current.plan and current.plan.speed_limit_upload else dl
            if not iptables.allow_device(current.mac_address, rate_kbps=dl, upload_kbps=ul):
                iptables.block_device(current.mac_address)
                return False
            granted = True
            current.resume_session()
            session.refresh_from_db()
        return True
    except Exception:
        logger.exception("Could not resume power-paused session %s", session.pk)
        if granted:
            # Compensate a rolled-back grant, without revoking a competing resume
            # that has already successfully committed.
            try:
                with transaction.atomic():
                    pending = Session.objects.filter(pk=session.pk, status="paused", power_paused=True)
                    if pending.update(power_paused=True):
                        _block_recovered([session.mac_address])
            except Exception:
                logger.exception("Could not reconcile firewall after failed power resume")
        return False


class PowerRecoveryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.http import HttpResponse
        try:
            ensure_power_recovery()
        except Exception:
            # Fail closed: don't let a dashboard/portal request expire sessions
            # while recovery is unavailable (e.g. migrations pending or bad clock).
            logger.exception("Power recovery unavailable; refusing session operations")
            return HttpResponse("Service is recovering. Please try again shortly.", status=503)
        return self.get_response(request)
