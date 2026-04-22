"""
iConnect — iptables Internet Access Control

Uses Linux iptables to manage device access to the internet.
In simulation mode (development), just logs the commands instead of executing them.
"""
import subprocess
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

SIMULATION_MODE = getattr(settings, 'PISONET_GPIO_SIMULATION', True)


def _ensure_forward_rule(rule_spec):
    """Ensure a FORWARD rule exists by checking first, then inserting at top."""
    check_cmd = ['iptables', '-C', 'FORWARD'] + rule_spec
    if _run_command(check_cmd, ignore_errors=True):
        return True

    add_cmd = ['iptables', '-I', 'FORWARD', '1'] + rule_spec
    return _run_command(add_cmd)


def _run_command(cmd, ignore_errors=False):
    """Execute an iptables command or log it in simulation mode."""
    if SIMULATION_MODE:
        logger.info(f'[SIMULATION] Would run: {" ".join(cmd)}')
        print(f'[SIMULATION] iptables: {" ".join(cmd)}')
        return True

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0 and not ignore_errors:
            logger.error(f'iptables error: {result.stderr}')
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error('iptables command timed out')
        return False
    except Exception as e:
        logger.error(f'iptables exception: {e}')
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
        logger.info(f'Device {mac_address} already allowed')
        return True

    if getattr(settings, 'PISONET_DNS_ONLY_PREAUTH', False):
        apply_pre_auth_dns_policy()

    mac = mac_address.upper()
    # Use -I (Insert) to put it at the top of the chain
    cmd = ['iptables', '-I', 'FORWARD', '1', '-m', 'mac', '--mac-source', mac, '-j', 'ACCEPT']
    success = _run_command(cmd)
    if success:
        logger.info(f'Allowed device: {mac}')
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
        logger.info(f'Blocked device: {mac}')

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



