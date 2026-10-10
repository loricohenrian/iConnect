"""Bandwidth usage tracking using real iptables and tc byte counters.

Reads actual byte counts from iptables and tc classes to get
real bidirectional bandwidth usage per device (MAC address and IP).
"""

import subprocess
import re
import logging
import math

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def _is_simulation():
    return getattr(settings, 'PISONET_GPIO_SIMULATION', False)


def _get_lan_interface():
    """Detect LAN interface or fallback."""
    lan = getattr(settings, 'PISONET_LAN_INTERFACE', '').strip()
    if lan:
        return lan
    portal_ip = getattr(settings, 'PISONET_PORTAL_IP', '').strip() or '10.10.10.1'
    try:
        result = subprocess.run(
            ['ip', 'addr'],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.split('\n'):
            if portal_ip in line and 'inet ' in line:
                parts = line.strip().split()
                if len(parts) >= 2:
                    return parts[-1]
    except Exception:
        pass
    return 'br0'


def _get_mac_to_ip_map():
    """Parse ARP table, dnsmasq DHCP leases, and active sessions to reliably map MAC to IP."""
    mac_to_ip = {}

    # 1. Parse ARP table (fast, kernel active cache)
    try:
        with open('/proc/net/arp', 'r') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4:
                    ip = parts[0]
                    flags = parts[2]
                    mac = parts[3].upper().strip()
                    if flags != '0x0' and len(mac) == 17:
                        mac_to_ip[mac] = ip
    except (OSError, IOError):
        pass

    # 2. Parse dnsmasq DHCP lease files (handles sleeping/paused phones that dropped from ARP)
    for lease_path in ('/var/lib/misc/dnsmasq.leases', '/tmp/dnsmasq.leases', '/var/run/dnsmasq.leases'):
        try:
            with open(lease_path, 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        mac = parts[1].upper().strip()
                        ip = parts[2].strip()
                        if len(mac) == 17 and ip and mac not in mac_to_ip:
                            mac_to_ip[mac] = ip
        except (OSError, IOError):
            pass

    # 3. Database session fallback (for active/paused sessions)
    try:
        from .models import Session
        for s in Session.objects.filter(status__in=['active', 'paused']).exclude(ip_address__isnull=True).exclude(ip_address='').values('mac_address', 'ip_address'):
            mac = (s['mac_address'] or '').upper().strip()
            ip = (s['ip_address'] or '').strip()
            if len(mac) == 17 and ip and mac not in mac_to_ip:
                mac_to_ip[mac] = ip
    except Exception:
        pass

    return mac_to_ip


def _get_ip_to_mac_map():
    """Parse ARP table to map IP address to MAC address."""
    ip_to_mac = {}
    for mac, ip in _get_mac_to_ip_map().items():
        ip_to_mac[ip] = mac
    return ip_to_mac


def _get_tc_class_bytes(iface):
    """Read byte counters from tc classes on a given interface.
    
    Runs: tc -s class show dev <iface>
    Returns dict: { classid_str: bytes_int, ... } e.g. { '1:100': 152000000 }
    """
    if _is_simulation() or not iface:
        return {}

    try:
        result = subprocess.run(
            ['tc', '-s', 'class', 'show', 'dev', iface],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None

        counters = {}
        current_class = None
        class_pattern = re.compile(r'class\s+\w+\s+(\d+:\d+)')
        sent_pattern = re.compile(r'Sent\s+(\d+)\s+bytes')

        for line in result.stdout.splitlines():
            line = line.strip()
            c_match = class_pattern.search(line)
            if c_match:
                current_class = c_match.group(1)
                continue
            if current_class:
                s_match = sent_pattern.search(line)
                if s_match:
                    try:
                        counters[current_class] = int(s_match.group(1))
                    except ValueError:
                        pass
                    current_class = None

        return counters
    except Exception as e:
        logger.debug('Failed to read tc class counters on %s: %s', iface, e)
        return None


def get_iptables_byte_counters():
    """Read real byte counters for both Upload and Download.
    
    Combines:
    1. iptables FORWARD chain rule counters (Upload via source MAC)
    2. iptables mangle POSTROUTING counters (Download via destination IP)
    3. tc class counters on LAN interface (Download via class 1:<mark>)
    
    Total = Upload + max(mangle_download, tc_download)
    
    Returns dict: { 'AA:BB:CC:DD:EE:FF': bytes_int, ... }, or None
    when a required counter source could not be read. A failed/partial
    snapshot must not masquerade as a counter reset.
    """
    if _is_simulation():
        return {}

    upload_by_mac = {}
    download_by_mac = {}
    mac_to_ip = _get_mac_to_ip_map()
    ip_to_mac = _get_ip_to_mac_map()

    # 1. Read iptables FORWARD chain (Upload by MAC)
    try:
        result = subprocess.run(
            ['iptables', '-L', 'FORWARD', '-v', '-n', '-x'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return None
        if result.returncode == 0:
            mac_pattern = re.compile(r'MAC\s+([0-9A-Fa-f:]{17})', re.IGNORECASE)
            for line in result.stdout.splitlines():
                line = line.strip()
                if 'ACCEPT' not in line:
                    continue
                mac_match = mac_pattern.search(line)
                if not mac_match:
                    continue
                mac = mac_match.group(1).upper()
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        byte_count = int(parts[1])
                        upload_by_mac[mac] = upload_by_mac.get(mac, 0) + byte_count
                    except ValueError:
                        continue
    except Exception as e:
        logger.error('Failed to read iptables FORWARD counters: %s', e)
        return None

    # 2. Read iptables mangle table (matches Download rules in POSTROUTING: -d <IP>)
    try:
        mangle_res = subprocess.run(
            ['iptables', '-t', 'mangle', '-L', 'POSTROUTING', '-v', '-n', '-x'],
            capture_output=True, text=True, timeout=5
        )
        if mangle_res.returncode != 0:
            return None
        if mangle_res.returncode == 0:
            ip_pattern = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')
            for line in mangle_res.stdout.splitlines():
                line = line.strip()
                if 'MARK' not in line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        byte_count = int(parts[1])
                        ips_in_line = ip_pattern.findall(line)
                        for dest_ip in ips_in_line:
                            if dest_ip in ip_to_mac:
                                mac = ip_to_mac[dest_ip]
                                download_by_mac[mac] = download_by_mac.get(mac, 0) + byte_count
                                break
                    except (ValueError, IndexError):
                        continue
    except Exception as e:
        logger.debug('Failed to read iptables mangle counters: %s', e)
        return None

    # 3. Read tc class counters on LAN interface for Download
    lan_iface = _get_lan_interface()
    lan_tc_bytes = _get_tc_class_bytes(lan_iface)
    if lan_tc_bytes is None:
        return None
    if lan_tc_bytes:
        for mac, ip in mac_to_ip.items():
            try:
                last_octet = int(ip.split('.')[-1])
                mark = max(10, min(254, last_octet))
                class_id = f'1:{mark}'
                dl_bytes = lan_tc_bytes.get(class_id, 0)
                if dl_bytes > 0:
                    current_dl = download_by_mac.get(mac, 0)
                    download_by_mac[mac] = max(current_dl, dl_bytes)
            except (ValueError, IndexError):
                continue

    # Combine Upload + Download
    all_macs = set(upload_by_mac.keys()) | set(download_by_mac.keys())
    counters = {}
    for mac in all_macs:
        ul = upload_by_mac.get(mac, 0)
        dl = download_by_mac.get(mac, 0)
        counters[mac] = ul + dl

    return counters


def get_device_bandwidth_mb(mac_address):
    """Get a device's counter in MB, or None for an unavailable/missing counter."""
    counters = get_iptables_byte_counters()
    mac = (mac_address or '').upper().strip()
    if counters is None or (mac not in counters and not _is_simulation()):
        return None
    return counters.get(mac, 0) / (1024 * 1024)


def get_all_device_bandwidth_mb():
    """Get bandwidth for all devices currently active.
    
    Returns list of dicts: [{'mac_address': 'XX:XX', 'bandwidth_mb': float}, ...]
    """
    counters = get_iptables_byte_counters() or {}
    result = []
    for mac, byte_count in counters.items():
        result.append({
            'mac_address': mac,
            'bandwidth_mb': round(byte_count / (1024 * 1024), 2),
        })
    return result


def refresh_session_bandwidth_usage(session, now=None):
    """Update session.bandwidth_used_mb from real iptables & tc byte counters.

    Accumulates bandwidth usage monotonically across active browsing,
    pause/resume cycles, and system reboots without ever wiping previous usage.
    """
    if not session or not session.pk or not session.mac_address:
        return False
    # Web polling and Celery can hold different, stale instances of the same
    # session. Lock and read its current baseline BEFORE sampling counters.
    with transaction.atomic():
        current = session.__class__.objects.select_for_update().filter(pk=session.pk).first()
        if current is None:
            return False
        real_mb = get_device_bandwidth_mb(current.mac_address)
        if real_mb is None or not math.isfinite(real_mb) or real_mb < 0:
            return False

        baseline = current.initial_bandwidth_mb
        current_used = float(current.bandwidth_used_mb or 0.0)
        # Unknown baselines are anchored without guessing how much traffic
        # preceded this session. For an unidentified partial counter reset,
        # re-anchor conservatively; known OS reboots reset anchors to zero in
        # power_recovery, while retaining the durable accumulated total.
        delta = real_mb - baseline if baseline is not None and real_mb >= baseline else 0.0
        # Keep sub-MB increments; round only for display, not on every poll.
        new_used = current_used + delta
        changed = new_used != current_used or baseline != real_mb
        if changed:
            current.bandwidth_used_mb = new_used
            current.initial_bandwidth_mb = real_mb
            current.save(update_fields=['bandwidth_used_mb', 'initial_bandwidth_mb'])
        session.bandwidth_used_mb = current.bandwidth_used_mb
        session.initial_bandwidth_mb = current.initial_bandwidth_mb
        return changed


_THROUGHPUT_CACHE_KEY = 'bw_snapshot'
_THROUGHPUT_TTL = 60  # seconds before snapshot expires


def get_live_throughput_mbps():
    """Compute real-time network throughput in Mbps by diffing iptables byte counter snapshots.

    On the first call it stores a snapshot and returns 0 Mbps.
    Subsequent calls diff the new snapshot against the stored one to compute speed.

    Returns a dict:
        {
            'total_mbps': float,           # combined up+down for all devices
            'by_mac': {'MAC': float, ...}, # per-device Mbps (only those > 0)
        }
    """
    import time
    try:
        from django.core.cache import cache
    except Exception:
        return {'total_mbps': 0.0, 'by_mac': {}}

    now_ts = time.time()
    current_counters = get_iptables_byte_counters()
    if current_counters is None:
        # Keep the last good snapshot: a failed read is not zero traffic.
        return {'total_mbps': 0.0, 'by_mac': {}}

    from .power_recovery import current_boot_id
    boot_id = current_boot_id()
    snapshot = cache.get(_THROUGHPUT_CACHE_KEY)
    if snapshot and snapshot.get('boot_id') != boot_id:
        # Redis can retain a pre-shutdown snapshot across an OS reboot.
        snapshot = None
    if snapshot and now_ts - snapshot['ts'] < 0.5:
        return {'total_mbps': 0.0, 'by_mac': {}}
    cache.set(_THROUGHPUT_CACHE_KEY, {'ts': now_ts, 'counters': current_counters,
                                    'boot_id': boot_id}, _THROUGHPUT_TTL)

    if not snapshot:
        # First call — no previous snapshot yet; return 0 and wait for next poll
        return {'total_mbps': 0.0, 'by_mac': {}}

    elapsed = now_ts - snapshot['ts']
    if elapsed < 0.5:
        # Interval too short — reading would be unreliable
        return {'total_mbps': 0.0, 'by_mac': {}}

    prev_counters = snapshot['counters']
    by_mac = {}
    total_bytes_delta = 0

    # Newly appearing counters have no comparable starting snapshot.
    all_macs = set(current_counters.keys()) & set(prev_counters.keys())
    for mac in all_macs:
        curr = current_counters.get(mac, 0)
        prev = prev_counters.get(mac, 0)
        delta = max(0, curr - prev)  # guard against counter resets
        total_bytes_delta += delta
        mbps = round((delta * 8) / (elapsed * 1_000_000), 3)  # bytes→bits→Mbps
        if mbps > 0:
            by_mac[mac] = mbps

    total_mbps = round((total_bytes_delta * 8) / (elapsed * 1_000_000), 3)
    return {'total_mbps': total_mbps, 'by_mac': by_mac}
