"""Read-only, dashboard-driven AP reachability checks. Never changes networking."""
import ipaddress
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from django.core.cache import cache
from django.utils import timezone


ACCESS_POINTS = (
    ('ap-1', 'EAP225-Outdoor #1', 'EC:B9:31:FB:4F:FA'),
    ('ap-2', 'EAP225-Outdoor #2', 'EC:B9:31:FB:5B:1E'),
)
INTERFACE = 'usblan0'
LAN = ipaddress.ip_network('10.10.10.0/24')
LEASE_FILE = Path('/var/lib/misc/dnsmasq.leases')
INTERVAL = 30
STALE_AFTER = 90
FAILURES_REQUIRED = 3
STATE_KEY = 'dashboard_access_points:v1'
LOCK_KEY = STATE_KEY + ':lock'


def _lan_ip(value):
    try:
        address = ipaddress.ip_address(value)
        if address in LAN and address not in (LAN.network_address, LAN.broadcast_address,
                                              ipaddress.ip_address('10.10.10.1')):
            return str(address)
    except (ValueError, TypeError):
        pass
    return None


def _resolve_addresses(now):
    """Use valid DHCP leases; a cached neighbor is only a candidate, never proof."""
    wanted = {mac for _, _, mac in ACCESS_POINTS}
    addresses = {}
    try:
        for line in LEASE_FILE.read_text().splitlines():
            fields = line.split()
            if len(fields) < 3 or fields[1].upper() not in wanted:
                continue
            try:
                expiry = int(fields[0])
            except ValueError:
                continue
            address = _lan_ip(fields[2])
            if address and (expiry == 0 or expiry > now.timestamp()):
                addresses[fields[1].upper()] = address
    except OSError:
        pass
    if len(addresses) < len(wanted):
        try:
            result = subprocess.run(['ip', '-j', '-4', 'neigh', 'show', 'dev', INTERFACE],
                                    capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                for item in json.loads(result.stdout):
                    mac = str(item.get('lladdr', '')).upper()
                    address = _lan_ip(item.get('dst'))
                    if mac in wanted and address and mac not in addresses:
                        addresses[mac] = address
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
            pass
    return addresses


def _probe(mac, address):
    """Return True/False for a completed probe, None if identity/tools are unknown."""
    if not address:
        return None
    try:
        result = subprocess.run(
            ['arping', '-I', INTERFACE, '-c', '1', '-w', '1', address],
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode not in (0, 1):
        return None
    # A different device at a recycled IP is not proof this AP is reachable.
    replies = re.findall(r'\[([0-9a-f:]{17})\]', result.stdout or '', flags=re.I)
    if mac in [reply.upper() for reply in replies] and result.returncode == 0:
        return True
    if replies or result.returncode == 0:
        return None
    return False


def _unknown(previous=None, reason='Check unavailable'):
    previous = previous or {}
    return {'id': previous.get('id'), 'name': previous.get('name'),
            'mac': previous.get('mac'), 'ip': previous.get('ip'), 'status': 'unknown',
            'reason': reason, 'last_checked': previous.get('last_checked'),
            'last_seen': previous.get('last_seen'), 'misses': 0}


def _initial():
    return [_unknown({'id': key, 'name': name, 'mac': mac})
            for key, name, mac in ACCESS_POINTS]


def _payload(state, now):
    age = now.timestamp() - state.get('checked_at', 0)
    rows = state.get('access_points', _initial())
    if age < 0 or age > STALE_AFTER:
        rows = [_unknown(row, 'Status outdated; waiting for a fresh check') for row in rows]
    return {'access_points': rows, 'poll_seconds': INTERVAL, 'stale_after_seconds': STALE_AFTER}


def get_access_point_status():
    """At most one bounded two-AP scan per 30 seconds across dashboard viewers.

    Checks happen while the dashboard is open, not via a new background worker.
    Redis stores only monitoring metadata; no database/customer records are edited.
    Cache/tool/address failures are Unknown, never reported as powered off.
    """
    now = timezone.now()
    token = uuid4().hex
    try:
        state = cache.get(STATE_KEY) or {}
        age = now.timestamp() - state.get('checked_at', 0)
        if 0 <= age < INTERVAL:
            return _payload(state, now)
        if not cache.add(LOCK_KEY, token, timeout=10):
            return _payload(state, now)
    except Exception:
        return {'access_points': _initial(), 'poll_seconds': INTERVAL,
                'stale_after_seconds': STALE_AFTER}
    try:
        # Another viewer may have finished a scan between our initial read and lock.
        try:
            state = cache.get(STATE_KEY) or {}
        except Exception:
            return {'access_points': _initial(), 'poll_seconds': INTERVAL,
                    'stale_after_seconds': STALE_AFTER}
        age = now.timestamp() - state.get('checked_at', 0)
        if 0 <= age < INTERVAL:
            return _payload(state, now)
        addresses = _resolve_addresses(now)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda ap: _probe(ap[2], addresses.get(ap[2])), ACCESS_POINTS))
        previous = {row['mac']: row for row in state.get('access_points', [])}
        rows = []
        for (key, name, mac), result in zip(ACCESS_POINTS, results):
            old = previous.get(mac, {})
            row = _unknown({'id': key, 'name': name, 'mac': mac, 'ip': addresses.get(mac),
                            'last_seen': old.get('last_seen'), 'last_checked': now.isoformat()})
            # Widely spaced or changed-IP misses must not accumulate as consecutive failures.
            misses = old.get('misses', 0) if (0 <= age <= STALE_AFTER and
                                              old.get('ip') == row['ip']) else 0
            if result is True:
                row.update(status='reachable', reason='Fresh reply from this AP MAC',
                           last_seen=now.isoformat())
            elif result is False:
                misses = min(misses + 1, FAILURES_REQUIRED)
                row.update(misses=misses, status='unreachable' if misses >= FAILURES_REQUIRED else 'unknown',
                           reason=f'No reply ({misses}/{FAILURES_REQUIRED} consecutive checks)')
            else:
                row['reason'] = ('Address not found in DHCP/neighbor records' if not row['ip']
                                 else 'Probe unavailable or device identity could not be verified')
            rows.append(row)
        state = {'checked_at': now.timestamp(), 'access_points': rows}
        try:
            cache.set(STATE_KEY, state, timeout=86400 * 7)
        except Exception:
            return {'access_points': [_unknown(row, 'Monitoring cache unavailable') for row in rows],
                    'poll_seconds': INTERVAL, 'stale_after_seconds': STALE_AFTER}
        return _payload(state, now)
    finally:
        try:
            if cache.get(LOCK_KEY) == token:
                cache.delete(LOCK_KEY)
        except Exception:
            pass
