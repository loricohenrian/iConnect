#!/usr/bin/env bash
# ==============================================================================
# iConnect - Fix Chrome No Internet and Google One
# ==============================================================================
# Run as root: sudo bash deploy/fix_connectivity.sh
# ==============================================================================

set -euo pipefail

if [ $(id -u) -ne 0 ]; then
    echo [ERROR] This script must be run as root: sudo bash deploy/fix_connectivity.sh
    exit 1
fi

echo ==============================================================================
echo  1. Removing DNS spoofing from dnsmasq configs...
echo ==============================================================================

# Remove hardcoded address=/.../ lines that poison connectivitycheck domains
for f in /etc/dnsmasq.conf /etc/dnsmasq.d/*.conf; do
    if [ -f $f ]; then
        if grep -qE address=/(connectivitycheck|captive\.apple|msftconnecttest) $f; then
            echo [*] Cleaning DNS spoofing rules from $f...
            sed -i '/address=\/connectivitycheck\.gstatic\.com/d' $f
            sed -i '/address=\/connectivitycheck\.android\.com/d' $f
            sed -i '/address=\/captive\.apple\.com/d' $f
            sed -i '/address=\/msftconnecttest\.com/d' $f
            echo [OK] Cleaned $f
        fi
    fi
done

# Restart dnsmasq to apply clean DNS
systemctl restart dnsmasq
echo [OK] dnsmasq restarted with clean DNS resolution.

echo "
echo ==============================================================================
echo  2. Cleaning Anti-Tethering TTL=1 rules that break Google One & VPN...
echo ==============================================================================

# Remove all TTL=1 mangle rules
while iptables -t mangle -D FORWARD -d 10.10.10.0/24 -j TTL --ttl-set 1 2>/dev/null; do :; done
while iptables -t mangle -D POSTROUTING -d 10.10.10.0/24 -j TTL --ttl-set 1 2>/dev/null; do :; done
while iptables -t mangle -D POSTROUTING -o usblan0 -j TTL --ttl-set 1 2>/dev/null; do :; done

# Remove TTL drop rules in PREROUTING
while iptables -t mangle -D PREROUTING -s 10.10.10.0/24 -m ttl --ttl-eq 63 -j DROP 2>/dev/null; do :; done
while iptables -t mangle -D PREROUTING -s 10.10.10.0/24 -m ttl --ttl-eq 127 -j DROP 2>/dev/null; do :; done
while iptables -t mangle -D PREROUTING -s 10.10.10.0/24 -m ttl --ttl-eq 254 -j DROP 2>/dev/null; do :; done

# Remove duplicate QUIC reject rules in FORWARD
while iptables -D FORWARD -p udp --dport 443 -j REJECT --reject-with icmp-port-unreachable 2>/dev/null; do :; done

echo [OK] Cleaned TTL mangle rules.

echo 
echo ==============================================================================
echo  3. Flushing stale conntrack entries for active clients...
echo ==============================================================================

# Flush conntrack so phones immediately connect to real Google IPs
conntrack -F 2>/dev/null || true
echo [OK] Flushed conntrack table.

echo 
echo ==============================================================================
echo  4. Verifying DNS resolution for connectivitycheck.gstatic.com...
echo ==============================================================================
nslookup connectivitycheck.gstatic.com 10.10.10.1 || true

echo 
echo ==============================================================================
echo  [SUCCESS] Fix applied! Chrome will now show 'Back online' and Google One will load.
echo ==============================================================================
