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


def allow_device(mac_address, rate_kbps=None, upload_kbps=None):
    """
    Allow a device to access the internet.
    Idempotent: Only adds the rule if it doesn't already exist.
    Also adds NAT bypass so HTTP/HTTPS traffic reaches the real internet.
    rate_kbps: download speed limit in kbps.
    upload_kbps: upload speed limit in kbps.
    """
    if is_device_allowed(mac_address):
        logger.info('Device %s already allowed', mac_address)
        _add_nat_bypass(mac_address)
        apply_bandwidth_limit(mac_address, rate_kbps=rate_kbps, upload_kbps=upload_kbps)
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
        apply_bandwidth_limit(mac, rate_kbps=rate_kbps, upload_kbps=upload_kbps)
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

    apply_network_settings()
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


def _get_lan_interface():
    """Detect the LAN (client-facing) interface."""
    lan = getattr(settings, 'PISONET_LAN_INTERFACE', '').strip()
    if lan:
        return lan
    # Auto-detect: interface with the portal IP (10.10.10.1)
    portal_ip = getattr(settings, 'PISONET_PORTAL_IP', '').strip() or '10.10.10.1'
    try:
        result = subprocess.run(
            ['ip', 'addr'],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.split('\n')
        for line in lines:
            if portal_ip in line and 'inet ' in line:
                # e.g., "    inet 10.10.10.1/24 brd 10.10.10.255 scope global usblan0"
                parts = line.strip().split()
                if len(parts) >= 2:
                    return parts[-1]  # The interface name is usually the last word
    except Exception:
        pass
    return 'br0'


def _ip_to_mark(ip):
    """Convert device IP to a unique mark/class ID (10-254) using last octet."""
    try:
        last_octet = int(ip.split('.')[-1])
        # Ensure we stay in range 10-254 to avoid conflicts with system marks
        return max(10, min(254, last_octet))
    except (ValueError, IndexError):
        return None


def _ensure_root_qdisc(iface):
    """Ensure the root HTB qdisc exists on an interface. Idempotent."""
    try:
        result = subprocess.run(
            ['tc', 'qdisc', 'show', 'dev', iface],
            capture_output=True, text=True, timeout=5
        )
        if 'htb 1:' in result.stdout:
            return True
    except Exception:
        pass

    # Create root qdisc
    ok = _run_command(['tc', 'qdisc', 'add', 'dev', iface, 'root', 'handle', '1:', 'htb', 'default', '9999'])
    if ok:
        # Default class for unthrottled traffic
        _run_command(['tc', 'class', 'add', 'dev', iface, 'parent', '1:', 'classid', '1:9999', 'htb',
                      'rate', '1000mbit', 'ceil', '1000mbit'])
    return ok


def _clean_mangle_rules(mac):
    """Remove all mangle FORWARD rules for a given MAC address."""
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

    # Also clean POSTROUTING rules (used for download marking by IP)
    try:
        result = subprocess.run(
            ['iptables', '-t', 'mangle', '-S', 'POSTROUTING'],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split('\n'):
            if 'MARK' in line:
                # We can't match by MAC here, need to match by mark value
                # This will be cleaned by IP-based removal below
                pass
    except Exception:
        pass


def _clean_ip_mangle_rules(ip):
    """Remove mangle rules that match a specific destination IP."""
    # Clean FORWARD rules
    try:
        result = subprocess.run(
            ['iptables', '-t', 'mangle', '-S', 'FORWARD'],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split('\n'):
            if ip in line and 'MARK' in line:
                parts = line.replace('-A ', '-D ').split()
                _run_command(['iptables', '-t', 'mangle'] + parts, ignore_errors=True)
    except Exception:
        pass

    # Clean POSTROUTING rules
    try:
        result = subprocess.run(
            ['iptables', '-t', 'mangle', '-S', 'POSTROUTING'],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split('\n'):
            if ip in line and 'MARK' in line:
                parts = line.replace('-A ', '-D ').split()
                _run_command(['iptables', '-t', 'mangle'] + parts, ignore_errors=True)
    except Exception:
        pass


def apply_bandwidth_limit(mac_address, rate_kbps=None, upload_kbps=None):
    """
    Apply per-device bandwidth limit with separate download and upload speeds.

    Download: tc on LAN interface (shapes what goes TO the user)
              iptables marks packets by destination IP
    Upload:   tc on WAN interface (shapes what goes FROM the user)
              iptables marks packets by source MAC

    rate_kbps:   download speed in kbps (default from settings)
    upload_kbps: upload speed in kbps (defaults to rate_kbps if not set)
    """
    if _is_simulation():
        logger.info('[SIM] Would apply bandwidth limit for %s', mac_address)
        return True

    if rate_kbps is None:
        rate_kbps = getattr(settings, 'PISONET_BANDWIDTH_LIMIT_KBPS', 2048)

    if upload_kbps is None:
        upload_kbps = rate_kbps  # default: same as download

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
    lan = _get_lan_interface()
    hex_mark = hex(mark)
    class_id = f'1:{mark}'
    dl_rate = f'{rate_kbps}kbit'
    ul_rate = f'{upload_kbps}kbit'

    # === CLEANUP old rules for this device ===
    _clean_mangle_rules(mac)
    _clean_ip_mangle_rules(ip)

    # === UPLOAD SHAPING (WAN egress — user → internet) ===
    _ensure_root_qdisc(wan)

    # Mark packets FROM this device (by MAC) for upload
    _run_command(['iptables', '-t', 'mangle', '-A', 'FORWARD',
                  '-m', 'mac', '--mac-source', mac, '-j', 'MARK', '--set-mark', hex_mark])

    # Create tc class on WAN with upload speed
    _run_command(['tc', 'class', 'replace', 'dev', wan, 'parent', '1:', 'classid', class_id, 'htb',
                  'rate', ul_rate, 'ceil', ul_rate])

    # Filter: marked packets → upload class
    _run_command(['tc', 'filter', 'del', 'dev', wan, 'parent', '1:', 'protocol', 'ip',
                  'handle', hex_mark, 'fw'], ignore_errors=True)
    _run_command(['tc', 'filter', 'add', 'dev', wan, 'parent', '1:', 'protocol', 'ip',
                  'handle', hex_mark, 'fw', 'classid', class_id])

    # === DOWNLOAD SHAPING (LAN egress — internet → user) ===
    _ensure_root_qdisc(lan)

    # Mark packets TO this device (by dest IP) for download
    # We use POSTROUTING so it catches both forwarded traffic and router-generated traffic
    _run_command(['iptables', '-t', 'mangle', '-A', 'POSTROUTING', '-o', lan,
                  '-d', ip, '-j', 'MARK', '--set-mark', hex_mark])

    # Create tc class on LAN with download speed
    _run_command(['tc', 'class', 'replace', 'dev', lan, 'parent', '1:', 'classid', class_id, 'htb',
                  'rate', dl_rate, 'ceil', dl_rate])

    # Filter: marked packets → download class
    _run_command(['tc', 'filter', 'del', 'dev', lan, 'parent', '1:', 'protocol', 'ip',
                  'handle', hex_mark, 'fw'], ignore_errors=True)
    _run_command(['tc', 'filter', 'add', 'dev', lan, 'parent', '1:', 'protocol', 'ip',
                  'handle', hex_mark, 'fw', 'classid', class_id])

    # === SQM CAKE (if enabled) ===
    try:
        from dashboard.models import SystemSettings
        settings_obj = SystemSettings.get_settings()
        if settings_obj.enable_sqm:
            # Attach CAKE to upload class
            _run_command(['tc', 'qdisc', 'replace', 'dev', wan, 'parent', class_id, 'handle', f'{mark}:', 'cake'], ignore_errors=True)
            # Attach CAKE to download class
            _run_command(['tc', 'qdisc', 'replace', 'dev', lan, 'parent', class_id, 'handle', f'{mark}:', 'cake'], ignore_errors=True)
    except Exception as e:
        logger.error(f"Error applying SQM CAKE: {e}")

    logger.info('Bandwidth limit applied for %s (IP=%s): DL=%s UL=%s', mac, ip, dl_rate, ul_rate)
    return True


def remove_bandwidth_limit(mac_address):
    """Remove per-device bandwidth limit from both WAN and LAN interfaces."""
    if _is_simulation():
        return True

    mac = mac_address.upper()
    ip = _get_device_ip(mac)
    wan = _get_wan_interface()
    lan = _get_lan_interface()

    # Remove ALL mangle rules for this MAC
    _clean_mangle_rules(mac)

    # Remove IP-based rules and tc classes/filters on both interfaces
    if ip:
        _clean_ip_mangle_rules(ip)
        mark = _ip_to_mark(ip)
        if mark:
            hex_mark = hex(mark)
            class_id = f'1:{mark}'

            # Clean WAN (upload)
            _run_command(['tc', 'filter', 'del', 'dev', wan, 'parent', '1:', 'protocol', 'ip',
                          'handle', hex_mark, 'fw'], ignore_errors=True)
            _run_command(['tc', 'class', 'del', 'dev', wan, 'parent', '1:', 'classid', class_id],
                         ignore_errors=True)

            # Clean LAN (download)
            _run_command(['tc', 'filter', 'del', 'dev', lan, 'parent', '1:', 'protocol', 'ip',
                          'handle', hex_mark, 'fw'], ignore_errors=True)
            _run_command(['tc', 'class', 'del', 'dev', lan, 'parent', '1:', 'classid', class_id],
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

def apply_network_settings():
    """Apply global network settings from SystemSettings."""
    if _is_simulation():
        logger.info("[SIM] Would apply network settings")
        return True
        
    try:
        from dashboard.models import SystemSettings
        settings_obj = SystemSettings.get_settings()
        lan = _get_lan_interface()
        
        # 1. Anti-Tethering (TTL = 1 on LAN + IPv6 HopLimit = 1)
        _run_command(['iptables', '-t', 'mangle', '-D', 'POSTROUTING', '-o', lan, '-j', 'TTL', '--ttl-set', '1'], ignore_errors=True)
        _run_command(['iptables', '-t', 'mangle', '-D', 'FORWARD', '-o', lan, '-j', 'TTL', '--ttl-set', '1'], ignore_errors=True)
        _run_command(['ip6tables', '-t', 'mangle', '-D', 'POSTROUTING', '-o', lan, '-j', 'HL', '--hl-set', '1'], ignore_errors=True)
            
        if settings_obj.enable_anti_tethering:
            _run_command(['iptables', '-t', 'mangle', '-A', 'POSTROUTING', '-o', lan, '-j', 'TTL', '--ttl-set', '1'])
            _run_command(['iptables', '-t', 'mangle', '-A', 'FORWARD', '-o', lan, '-j', 'TTL', '--ttl-set', '1'], ignore_errors=True)
            _run_command(['ip6tables', '-t', 'mangle', '-A', 'POSTROUTING', '-o', lan, '-j', 'HL', '--hl-set', '1'], ignore_errors=True)
            logger.info("Anti-Tethering (TTL=1 / HL=1) enabled on LAN")
        else:
            logger.info("Anti-Tethering disabled")
            
        # 2. SQM CAKE - For existing users, they will get CAKE when they reconnect or get re-allowed.
        # But we also want to limit the root default unthrottled traffic to the ISP limits to prevent global bufferbloat
        wan = _get_wan_interface()
        
        _ensure_root_qdisc(wan)
        _ensure_root_qdisc(lan)
        
        if settings_obj.enable_sqm:
            isp_dl = f"{settings_obj.isp_download_speed}mbit"
            isp_ul = f"{settings_obj.isp_upload_speed}mbit"
            
            # Update default class (1:9999) to ISP limit instead of 1000mbit
            _run_command(['tc', 'class', 'replace', 'dev', wan, 'parent', '1:', 'classid', '1:9999', 'htb', 'rate', isp_ul, 'ceil', isp_ul])
            _run_command(['tc', 'class', 'replace', 'dev', lan, 'parent', '1:', 'classid', '1:9999', 'htb', 'rate', isp_dl, 'ceil', isp_dl])
            
            # Attach CAKE to the default classes
            _run_command(['tc', 'qdisc', 'replace', 'dev', wan, 'parent', '1:9999', 'handle', '9999:', 'cake'], ignore_errors=True)
            _run_command(['tc', 'qdisc', 'replace', 'dev', lan, 'parent', '1:9999', 'handle', '9999:', 'cake'], ignore_errors=True)
            logger.info(f"SQM enabled: WAN default={isp_ul}, LAN default={isp_dl}")
        else:
            # Restore to unrestricted default
            _run_command(['tc', 'class', 'replace', 'dev', wan, 'parent', '1:', 'classid', '1:9999', 'htb', 'rate', '1000mbit', 'ceil', '1000mbit'])
            _run_command(['tc', 'class', 'replace', 'dev', lan, 'parent', '1:', 'classid', '1:9999', 'htb', 'rate', '1000mbit', 'ceil', '1000mbit'])
            # Remove CAKE
            _run_command(['tc', 'qdisc', 'del', 'dev', wan, 'parent', '1:9999'], ignore_errors=True)
            _run_command(['tc', 'qdisc', 'del', 'dev', lan, 'parent', '1:9999'], ignore_errors=True)
            logger.info("SQM disabled")
            
        return True
    except Exception as e:
        logger.error(f"Error applying network settings: {e}")
        return False
