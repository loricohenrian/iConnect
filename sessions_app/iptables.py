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
        if result.returncode != 0 and not ignore_errors:
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


def allow_device(mac_address):
    """
    Allow a device to access the internet.
    Idempotent: Only adds the rule if it doesn't already exist.
    """
    if is_device_allowed(mac_address):
        logger.info('Device %s already allowed', mac_address)
        return True

    if getattr(settings, 'PISONET_DNS_ONLY_PREAUTH', False):
        apply_pre_auth_dns_policy()

    mac = mac_address.upper()
    # Use -I (Insert) to put it at the top of the chain
    cmd = ['iptables', '-I', 'FORWARD', '1', '-m', 'mac', '--mac-source', mac, '-j', 'ACCEPT']
    success = _run_command(cmd)
    if success:
        logger.info('Allowed device: %s', mac)
    return success


def block_device(mac_address):
    """
    Remove a device's internet access.
    Ensures all duplicate instances of the rule are removed.
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
