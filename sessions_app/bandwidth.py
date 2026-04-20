"""Bandwidth usage estimation helpers for active sessions."""

from django.conf import settings
from django.utils import timezone


DEFAULT_SPEED_CAP_MBPS = 10.0
DEFAULT_UTILIZATION_RATIO = 0.35
MIN_EFFECTIVE_MBPS = 0.5


def estimate_session_bandwidth_mb(session, now=None):
    """Estimate cumulative bandwidth (MB) for an active session.

    The estimate is elapsed_time_seconds * effective_throughput_mbps / 8.
    """
    now = now or timezone.now()
    if session.status != "active" or not session.time_in:
        return float(session.bandwidth_used_mb or 0)

    cap_mbps = float(session.plan.speed_limit) if session.plan and session.plan.speed_limit else DEFAULT_SPEED_CAP_MBPS
    utilization_ratio = float(getattr(settings, "PISONET_ESTIMATED_UTILIZATION_RATIO", DEFAULT_UTILIZATION_RATIO))
    effective_mbps = max(MIN_EFFECTIVE_MBPS, cap_mbps * utilization_ratio)

    elapsed_seconds = max(0.0, (now - session.time_in).total_seconds())
    estimated_mb = (elapsed_seconds * effective_mbps) / 8.0
    return round(estimated_mb, 2)


def refresh_session_bandwidth_usage(session, now=None):
    """Update session.bandwidth_used_mb if the latest estimate is higher."""
    estimated = estimate_session_bandwidth_mb(session, now=now)
    current = float(session.bandwidth_used_mb or 0)
    if estimated > current:
        session.bandwidth_used_mb = estimated
        session.save(update_fields=["bandwidth_used_mb"])
        return True
    return False
