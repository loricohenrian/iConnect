"""Bandwidth usage tracking using real iptables and tc byte counters.

Reads actual byte counts from iptables and tc classes to get
real bidirectional bandwidth usage per device (MAC address and IP).
"""

import subprocess
import re
import logging

from django.conf import settings
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
    """Parse ARP table to map MAC address to current IP address."""
    mac_to_ip = {}
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
            return {}

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
        return {}


def get_iptables_byte_counters():
    """Read real byte counters for both Upload and Download.
    
    Combines:
    1. iptables FORWARD chain rule counters (Upload via source MAC)
    2. iptables mangle POSTROUTING counters (Download via destination IP)
    3. tc class counters on LAN interface (Download via class 1:<mark>)
    
    Total = Upload + max(mangle_download, tc_download)
    
    Returns dict: { 'AA:BB:CC:DD:EE:FF': bytes_int, ... }
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

    # 2. Read iptables mangle table (matches Download rules in POSTROUTING: -d <IP>)
    try:
        mangle_res = subprocess.run(
            ['iptables', '-t', 'mangle', '-L', 'POSTROUTING', '-v', '-n', '-x'],
            capture_output=True, text=True, timeout=5
        )
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

    # 3. Read tc class counters on LAN interface for Download
    lan_iface = _get_lan_interface()
    lan_tc_bytes = _get_tc_class_bytes(lan_iface)
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
    """Get real bandwidth usage in MB for a specific device (Upload + Download)."""
    counters = get_iptables_byte_counters()
    mac = (mac_address or '').upper().strip()
    byte_count = counters.get(mac, 0)
    return round(byte_count / (1024 * 1024), 2)


def get_all_device_bandwidth_mb():
    """Get bandwidth for all devices currently active.
    
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
    """Update session.bandwidth_used_mb from real iptables & tc byte counters."""
    if not session or not session.mac_address:
        return False
    real_mb = get_device_bandwidth_mb(session.mac_address)
    current = float(session.bandwidth_used_mb or 0)
    if real_mb > current:
        session.bandwidth_used_mb = real_mb
        session.save(update_fields=["bandwidth_used_mb"])
        return True
    return False
