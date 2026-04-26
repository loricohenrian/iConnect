"""Bandwidth usage tracking using real iptables byte counters.

Reads actual byte counts from iptables FORWARD chain rules to get
real bandwidth usage per device (MAC address).
"""

import subprocess
import re
import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _is_simulation():
    return getattr(settings, 'PISONET_GPIO_SIMULATION', False)


def get_iptables_byte_counters():
    """Read iptables FORWARD chain with verbose output to get byte counters.
    
    Runs: iptables -L FORWARD -v -n -x
    Parses output to get bytes per MAC-based ACCEPT rule.
    
    Returns dict: { 'AA:BB:CC:DD:EE:FF': bytes_int, ... }
    """
    if _is_simulation():
        return {}

    try:
        result = subprocess.run(
            ['iptables', '-L', 'FORWARD', '-v', '-n', '-x'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            logger.error('iptables -L failed: %s', result.stderr)
            return {}

        counters = {}
        # Each line looks like:
        # pkts bytes target prot opt in out source destination  ... MAC AA:BB:CC:DD:EE:FF
        mac_pattern = re.compile(r'MAC\s+([0-9A-Fa-f:]{17})', re.IGNORECASE)
        for line in result.stdout.splitlines():
            line = line.strip()
            if 'ACCEPT' not in line:
                continue
            mac_match = mac_pattern.search(line)
            if not mac_match:
                continue
            mac = mac_match.group(1).upper()
            # Parse byte count (second numeric column)
            parts = line.split()
            if len(parts) >= 2:
                try:
                    byte_count = int(parts[1])
                    # Accumulate if multiple rules for same MAC
                    counters[mac] = counters.get(mac, 0) + byte_count
                except ValueError:
                    continue

        return counters
    except subprocess.TimeoutExpired:
        logger.error('iptables command timed out')
        return {}
    except Exception as e:
        logger.error('Failed to read iptables counters: %s', e)
        return {}


def get_device_bandwidth_mb(mac_address):
    """Get real bandwidth usage in MB for a specific device."""
    counters = get_iptables_byte_counters()
    mac = (mac_address or '').upper().strip()
    byte_count = counters.get(mac, 0)
    return round(byte_count / (1024 * 1024), 2)


def get_all_device_bandwidth_mb():
    """Get bandwidth for all devices currently in iptables.
    
    Returns list of dicts: [{'mac_address': 'XX:XX', 'bandwidth_mb': float}, ...]
    """
    counters = get_iptables_byte_counters()
    result = []
    for mac, byte_count in counters.items():
        result.append({
            'mac_address': mac,
            'bandwidth_mb': round(byte_count / (1024 * 1024), 2),
        })
    return result


def refresh_session_bandwidth_usage(session, now=None):
    """Update session.bandwidth_used_mb from real iptables byte counters."""
    real_mb = get_device_bandwidth_mb(session.mac_address)
    current = float(session.bandwidth_used_mb or 0)
    if real_mb > current:
        session.bandwidth_used_mb = real_mb
        session.save(update_fields=["bandwidth_used_mb"])
        return True
    return False
