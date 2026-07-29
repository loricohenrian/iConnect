"""
iConnect — iptables Internet Access Control

Uses Linux iptables to manage device access to the internet.
When not on Linux (development), commands are logged but not executed.
"""
import subprocess
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def _is_simulation():
    """Check simulation mode dynamically from settings each call."""
    return getattr(settings, 'PISONET_GPIO_SIMULATION', False)


def _run_command_capture(cmd):
    """Execute a command and return a completed process (or None on exception)."""
    if _is_simulation():
        logger.info('[SIM] Would run: %s', ' '.join(cmd))
        return None

    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        logger.error('iptables command timed out')
        return None
    except Exception as e:
        logger.error('iptables exception: %s', e)
        return None


def _ensure_forward_rule(rule_spec):
    """Ensure a FORWARD rule exists by checking first, then inserting at top."""
    check_cmd = ['iptables', '-C', 'FORWARD'] + rule_spec
    if _run_command(check_cmd, ignore_errors=True):
        return True

    add_cmd = ['iptables', '-I', 'FORWARD', '1'] + rule_spec
    return _run_command(add_cmd)


def _run_command(cmd, ignore_errors=False):
    """Execute an iptables command or log it in simulation mode."""
    if _is_simulation():
        logger.info('[SIM] Would run: %s', ' '.join(cmd))
        return True

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            if not ignore_errors:
                logger.error('iptables error: %s', result.stderr)
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error('iptables command timed out')
        return False
    except Exception as e:
        logger.error('iptables exception: %s', e)
        return False


def is_device_allowed(mac_address):
    """Check if a device is already allowed in iptables."""
    mac = mac_address.upper()
    cmd = ['iptables', '-C', 'FORWARD', '-m', 'mac', '--mac-source', mac, '-j', 'ACCEPT']
    return _run_command(cmd, ignore_errors=True)


def _is_nat_bypass_set(mac_address):
    """Check if NAT PREROUTING bypass exists for a device."""
    mac = mac_address.upper()
    cmd = ['iptables', '-t', 'nat', '-C', 'PREROUTING', '-m', 'mac', '--mac-source', mac, '-j', 'RETURN']
    return _run_command(cmd, ignore_errors=True)


def _add_nat_bypass(mac_address):
    """Add NAT PREROUTING bypass so authenticated device traffic goes to real internet."""
    mac = mac_address.upper()
    if _is_nat_bypass_set(mac):
        return True
    cmd = ['iptables', '-t', 'nat', '-I', 'PREROUTING', '1', '-m', 'mac', '--mac-source', mac, '-j', 'RETURN']
    success = _run_command(cmd)
    if success:
        logger.info('NAT bypass added for device: %s', mac)
    return success


def _remove_nat_bypass(mac_address):
    """Remove NAT PREROUTING bypass so device traffic gets redirected to captive portal."""
    mac = mac_address.upper()
    cmd = ['iptables', '-t', 'nat', '-D', 'PREROUTING', '-m', 'mac', '--mac-source', mac, '-j', 'RETURN']
    removed = False
    while _is_nat_bypass_set(mac):
        if _run_command(cmd):
            removed = True
        else:
            break
    if removed:
        logger.info('NAT bypass removed for device: %s', mac)
    return True


def allow_device(mac_address, rate_kbps=None):
    """
    Allow a device to access the internet.
    Idempotent: Only adds the rule if it doesn't already exist.
    Also adds NAT bypass so HTTP/HTTPS traffic reaches the real internet.
    rate_kbps: optional per-device bandwidth limit (from plan's speed_limit).
    """
    if is_device_allowed(mac_address):
        logger.info('Device %s already allowed', mac_address)
        _add_nat_bypass(mac_address)
        apply_bandwidth_limit(mac_address, rate_kbps=rate_kbps)
        return True

    if getattr(settings, 'PISONET_DNS_ONLY_PREAUTH', False):
        apply_pre_auth_dns_policy()

    mac = mac_address.upper()
    # Use -I (Insert) to put it at the top of the chain
    cmd = ['iptables', '-I', 'FORWARD', '1', '-m', 'mac', '--mac-source', mac, '-j', 'ACCEPT']
    success = _run_command(cmd)
    if success:
        logger.info('Allowed device: %s', mac)
        _add_nat_bypass(mac)
        apply_bandwidth_limit(mac, rate_kbps=rate_kbps)
    return success


def _flush_conntrack(mac_address):
    """Flush connection tracking entries for a device to kill established connections."""
    mac = mac_address.upper()
    # Get device IP from ARP table, then flush conntrack by IP
    try:
        with open('/proc/net/arp', 'r') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[3].upper() == mac:
                    ip = parts[0]
                    _run_command(['conntrack', '-D', '-s', ip], ignore_errors=True)
                    _run_command(['conntrack', '-D', '-d', ip], ignore_errors=True)
                    logger.info('Flushed conntrack for device: %s (%s)', mac, ip)
                    return True
    except (OSError, IOError):
        pass
    return False


def block_device(mac_address):
    """
    Remove a device's internet access.
    Ensures all duplicate instances of the rule are removed.
    Also removes NAT bypass so device gets redirected to captive portal.
    Kills existing connections so apps lose internet immediately.
    """
    mac = mac_address.upper()
    cmd = ['iptables', '-D', 'FORWARD', '-m', 'mac', '--mac-source', mac, '-j', 'ACCEPT']

    # Keep deleting until no more such rules exist (to handle potential legacy duplicates)
    deleted = False
    while is_device_allowed(mac_address):
        if _run_command(cmd):
            deleted = True
        else:
            break

    if deleted:
        logger.info('Blocked device: %s', mac)

    # Remove NAT bypass so traffic gets redirected to captive portal
    _remove_nat_bypass(mac_address)

    # Remove bandwidth limit
    remove_bandwidth_limit(mac_address)

    # Kill all established connections for this device
    _flush_conntrack(mac_address)

    if getattr(settings, 'PISONET_DNS_ONLY_PREAUTH', False):
        apply_pre_auth_dns_policy()
    return True


def setup_default_policy():
    """
    Set default FORWARD policy to DROP.
    All devices are blocked unless explicitly allowed.
    """
    cmd = ['iptables', '-P', 'FORWARD', 'DROP']
    success = _run_command(cmd)
    if success:
        logger.info('Default FORWARD policy set to DROP')
    return success


def get_forward_default_policy():
    """Return FORWARD chain default policy (e.g., DROP/ACCEPT), or None if unknown."""
    if _is_simulation():
        return 'DROP'

    result = _run_command_capture(['iptables', '-S', 'FORWARD'])
    if result is None:
        return None

    if result.returncode != 0:
        logger.error('Failed to read FORWARD policy: %s', result.stderr)
        return None

    for line in result.stdout.splitlines():
        # Expected format: -P FORWARD DROP
        tokens = line.strip().split()
        if len(tokens) == 3 and tokens[0] == '-P' and tokens[1] == 'FORWARD':
            return tokens[2].upper()

    logger.warning('Unable to detect FORWARD default policy from iptables output')
    return None


def is_forward_default_drop():
    """True when FORWARD default policy is DROP."""
    return get_forward_default_policy() == 'DROP'


def enforce_firewall_baseline():
    """Enforce baseline policy and verify FORWARD default policy is DROP."""
    if getattr(settings, 'PISONET_DNS_ONLY_PREAUTH', False):
        baseline_ok = apply_pre_auth_dns_policy()
    else:
        baseline_ok = setup_default_policy()

    if not baseline_ok:
        logger.error('Failed to apply firewall baseline rules')
        return False

    if not is_forward_default_drop():
        logger.error('FORWARD default policy is not DROP after baseline enforcement')
        return False

    logger.info('Firewall baseline verified: FORWARD policy is DROP')
    return True


def apply_pre_auth_dns_policy():
    """Enforce DNS-only pre-auth baseline plus optional captive portal access."""
    if not getattr(settings, 'PISONET_DNS_ONLY_PREAUTH', False):
        return True

    dns_resolver = getattr(settings, 'PISONET_DNS_RESOLVER', '').strip()
    portal_ip = getattr(settings, 'PISONET_PORTAL_IP', '').strip()

    if not setup_default_policy():
        return False

    dns_udp_rule = ['-p', 'udp']
    dns_tcp_rule = ['-p', 'tcp']
    if dns_resolver:
        dns_udp_rule += ['-d', dns_resolver]
        dns_tcp_rule += ['-d', dns_resolver]
    dns_udp_rule += ['--dport', '53', '-j', 'ACCEPT']
    dns_tcp_rule += ['--dport', '53', '-j', 'ACCEPT']

    rules = [
        ['-m', 'conntrack', '--ctstate', 'RELATED,ESTABLISHED', '-j', 'ACCEPT'],
        dns_udp_rule,
        dns_tcp_rule,
    ]

    if portal_ip:
        rules.append(['-p', 'tcp', '-d', portal_ip, '--dport', '80', '-j', 'ACCEPT'])
        rules.append(['-p', 'tcp', '-d', portal_ip, '--dport', '443', '-j', 'ACCEPT'])

    applied = True
    for rule in rules:
        applied = _ensure_forward_rule(rule) and applied

    if applied:
        logger.info('Applied DNS-only pre-auth policy (resolver=%s, portal_ip=%s)', dns_resolver or 'ANY', portal_ip or 'unset')
    else:
        logger.warning('Failed to fully apply DNS-only pre-auth policy')
    return applied


def _get_device_ip(mac_address):
    """Resolve MAC address to IP from ARP table."""
    mac = mac_address.upper()
    try:
        with open('/proc/net/arp', 'r') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[3].upper() == mac:
                    return parts[0]
    except (OSError, IOError):
        pass
    return None


def _get_wan_interface():
    """Detect the WAN (internet-facing) interface."""
    wan = getattr(settings, 'PISONET_WAN_INTERFACE', '').strip()
    if wan:
        return wan
    # Auto-detect: interface with default route
    try:
        result = subprocess.run(
            ['ip', 'route', 'show', 'default'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            if 'dev' in parts:
                return parts[parts.index('dev') + 1]
    except Exception:
        pass
    return 'eth0'


def _ip_to_mark(ip):
    """Convert device IP to a unique mark/class ID (10-254) using last octet."""
    try:
        last_octet = int(ip.split('.')[-1])
        # Ensure we stay in range 10-254 to avoid conflicts with system marks
        return max(10, min(254, last_octet))
    except (ValueError, IndexError):
        return None


def _ensure_root_qdisc(wan):
    """Ensure the root HTB qdisc exists on the WAN interface. Idempotent."""
    # Check if root qdisc already exists
    result = subprocess.run(
        ['tc', 'qdisc', 'show', 'dev', wan],
        capture_output=True, text=True, timeout=5
    )
    if 'htb 1:' in result.stdout:
        return True

    # Create root qdisc
    ok = _run_command(['tc', 'qdisc', 'add', 'dev', wan, 'root', 'handle', '1:', 'htb', 'default', '9999'])
    if ok:
        # Default class for unthrottled traffic (system, whitelisted, etc.)
        _run_command(['tc', 'class', 'add', 'dev', wan, 'parent', '1:', 'classid', '1:9999', 'htb',
                      'rate', '100mbit', 'ceil', '100mbit'])
    return ok


def apply_bandwidth_limit(mac_address, rate_kbps=None):
    """
    Apply per-device bandwidth limit using iptables mark + tc class.
    Each device gets a UNIQUE mark and its own tc class, so speeds are independent.
    rate_kbps: max speed in kilobits/sec (default from settings, fallback 2048 = 2Mbps)
    """
    if _is_simulation():
        logger.info('[SIM] Would apply bandwidth limit for %s', mac_address)
        return True

    if rate_kbps is None:
        rate_kbps = getattr(settings, 'PISONET_BANDWIDTH_LIMIT_KBPS', 2048)

    mac = mac_address.upper()
    ip = _get_device_ip(mac)
    if not ip:
        logger.warning('Cannot apply bandwidth limit: no IP for %s', mac)
        return False

    mark = _ip_to_mark(ip)
    if not mark:
        logger.warning('Cannot derive mark from IP %s for %s', ip, mac)
        return False

    wan = _get_wan_interface()
    hex_mark = hex(mark)
    class_id = f'1:{mark}'
    rate_str = f'{rate_kbps}kbit'

    # 1. Ensure root HTB qdisc exists
    _ensure_root_qdisc(wan)

    # 2. Remove old mangle mark for this device (if any) to avoid duplicates
    old_mark_rule = ['-m', 'mac', '--mac-source', mac, '-j', 'MARK', '--set-mark']
    try:
        result = subprocess.run(
            ['iptables', '-t', 'mangle', '-S', 'FORWARD'],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split('\n'):
            if mac in line and 'MARK' in line:
                # Extract the full rule and delete it
                parts = line.replace('-A ', '-D ').split()
                _run_command(['iptables', '-t', 'mangle'] + parts, ignore_errors=True)
    except Exception:
        pass

    # 3. Add iptables mangle rule: mark this device's packets with unique mark
    mark_rule = ['-m', 'mac', '--mac-source', mac, '-j', 'MARK', '--set-mark', hex_mark]
    _run_command(['iptables', '-t', 'mangle', '-A', 'FORWARD'] + mark_rule)

    # 4. Create/replace tc class for this device with its plan speed
    #    'replace' creates if missing, updates if exists
    _run_command(['tc', 'class', 'replace', 'dev', wan, 'parent', '1:', 'classid', class_id, 'htb',
                  'rate', rate_str, 'ceil', rate_str])

    # 5. Add tc filter: route packets with this mark to this device's class
    #    Delete old filter first (ignore error if doesn't exist)
    _run_command(['tc', 'filter', 'del', 'dev', wan, 'parent', '1:', 'protocol', 'ip',
                  'handle', hex_mark, 'fw'], ignore_errors=True)
    _run_command(['tc', 'filter', 'add', 'dev', wan, 'parent', '1:', 'protocol', 'ip',
                  'handle', hex_mark, 'fw', 'classid', class_id])

    logger.info('Bandwidth limit applied for %s (IP=%s mark=%s): %s kbps', mac, ip, hex_mark, rate_kbps)
    return True


def remove_bandwidth_limit(mac_address):
    """Remove per-device bandwidth limit: iptables mark + tc class + tc filter."""
    if _is_simulation():
        return True

    mac = mac_address.upper()
    ip = _get_device_ip(mac)
    wan = _get_wan_interface()

    # Remove ALL mangle rules for this MAC (handles any mark value)
    try:
        result = subprocess.run(
            ['iptables', '-t', 'mangle', '-S', 'FORWARD'],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split('\n'):
            if mac in line and 'MARK' in line:
                parts = line.replace('-A ', '-D ').split()
                _run_command(['iptables', '-t', 'mangle'] + parts, ignore_errors=True)
    except Exception:
        pass

    # Remove tc class and filter for this device if we know the IP
    if ip:
        mark = _ip_to_mark(ip)
        if mark:
            hex_mark = hex(mark)
            class_id = f'1:{mark}'
            _run_command(['tc', 'filter', 'del', 'dev', wan, 'parent', '1:', 'protocol', 'ip',
                          'handle', hex_mark, 'fw'], ignore_errors=True)
            _run_command(['tc', 'class', 'del', 'dev', wan, 'parent', '1:', 'classid', class_id],
                         ignore_errors=True)

    logger.info('Bandwidth limit removed for %s', mac)
    return True


def whitelist_device(mac_address):
    """
    Permanently whitelist a device. Idempotent.
    """
    return allow_device(mac_address)


def flush_rules():
    """Remove all FORWARD rules. Use with caution."""
    cmd = ['iptables', '-F', 'FORWARD']
    success = _run_command(cmd)
    if success:
        logger.info('Flushed all FORWARD rules')
    return success
