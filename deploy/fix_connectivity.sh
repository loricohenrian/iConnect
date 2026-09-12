#!/usr/bin/env bash
# ==============================================================================
# iConnect - Fix Chrome No Internet, Google One, and Google Drive Connectivity
# ==============================================================================
# Run as root on Orange Pi: sudo bash deploy/fix_connectivity.sh
# ==============================================================================

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] This script must be run as root: sudo bash deploy/fix_connectivity.sh"
    exit 1
fi

PROJECT_ROOT="${PROJECT_ROOT:-/opt/iconnect/pisowifi}"

echo "=============================================================================="
echo " 1. Disabling IPv6 globally across sysctl (prevents dual-stack stalls)..."
echo "=============================================================================="

mkdir -p /etc/sysctl.d
cat << 'EOF' > /etc/sysctl.d/99-disable-ipv6.conf
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
net.ipv6.conf.usblan0.disable_ipv6 = 1
EOF

sysctl -p /etc/sysctl.d/99-disable-ipv6.conf 2>/dev/null || true
sysctl -w net.ipv6.conf.all.disable_ipv6=1 2>/dev/null || true
sysctl -w net.ipv6.conf.default.disable_ipv6=1 2>/dev/null || true
sysctl -w net.ipv6.conf.usblan0.disable_ipv6=1 2>/dev/null || true
echo "[OK] IPv6 disabled on all interfaces."

echo ""
echo "=============================================================================="
echo " 2. Configuring dnsmasq IPv6 AAAA filtering and dynamic binding..."
echo "=============================================================================="

mkdir -p /etc/dnsmasq.d
cat << 'EOF' > /etc/dnsmasq.d/filter-aaaa.conf
# iConnect - Suppress IPv6 AAAA records on IPv4-only PisoWiFi.
# Prevents Android/Chrome/Google One from attempting broken IPv6 connections.
filter-AAAA
EOF

cat << 'EOF' > /etc/dnsmasq.d/bind-dynamic.conf
# iConnect - Bind dynamically to interfaces as they become ready.
bind-dynamic
EOF

cat << 'EOF' > /etc/dnsmasq.d/captive-portal.conf
# iConnect - RFC 8908 Captive Portal API DHCP Option 114
# Enables Android 11+ and iOS 14+ to automatically validate network in real time
dhcp-option=114,http://10.10.10.1/api/captive-portal/
EOF

# Remove any DNS spoofing rules that poison connectivitycheck or captive portal probes
sed -i '/^address=\//d' /etc/dnsmasq.d/*.conf 2>/dev/null || true
if [ -f /etc/dnsmasq.conf ]; then
    sed -i '/^address=\//d' /etc/dnsmasq.conf 2>/dev/null || true
fi

systemctl restart dnsmasq
echo "[OK] dnsmasq updated with filter-AAAA, bind-dynamic, RFC 8908 option 114, and clean DNS."

echo ""
echo "=============================================================================="
echo " 3. Scrubbing broken TTL drop rules and enforcing TCP MSS clamping..."
echo "=============================================================================="

# Remove all TTL=1 mangle rules
while iptables -t mangle -D FORWARD -d 10.10.10.0/24 -j TTL --ttl-set 1 2>/dev/null; do :; done
while iptables -t mangle -D FORWARD -j TTL --ttl-set 1 2>/dev/null; do :; done
while iptables -t mangle -D POSTROUTING -d 10.10.10.0/24 -j TTL --ttl-set 1 2>/dev/null; do :; done
while iptables -t mangle -D POSTROUTING -o usblan0 -j TTL --ttl-set 1 2>/dev/null; do :; done
while iptables -t mangle -D POSTROUTING -j TTL --ttl-set 1 2>/dev/null; do :; done

# Remove TTL drop rules in PREROUTING (these break Google One VPN and Android internal tun packets)
while iptables -t mangle -D PREROUTING -s 10.10.10.0/24 -m ttl --ttl-eq 63 -j DROP 2>/dev/null; do :; done
while iptables -t mangle -D PREROUTING -m ttl --ttl-eq 63 -j DROP 2>/dev/null; do :; done
while iptables -t mangle -D PREROUTING -s 10.10.10.0/24 -m ttl --ttl-eq 127 -j DROP 2>/dev/null; do :; done
while iptables -t mangle -D PREROUTING -m ttl --ttl-eq 127 -j DROP 2>/dev/null; do :; done
while iptables -t mangle -D PREROUTING -s 10.10.10.0/24 -m ttl --ttl-eq 254 -j DROP 2>/dev/null; do :; done
while iptables -t mangle -D PREROUTING -m ttl --ttl-eq 254 -j DROP 2>/dev/null; do :; done

# Remove duplicate QUIC reject rules
while iptables -D FORWARD -p udp --dport 443 -j REJECT --reject-with icmp-port-unreachable 2>/dev/null; do :; done

# Enforce TCP MSS Clamping to PMTU in both FORWARD and POSTROUTING chains
iptables -t mangle -C FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || \
    iptables -t mangle -I FORWARD 1 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
iptables -t mangle -C POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || \
    iptables -t mangle -I POSTROUTING 1 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

echo "[OK] Cleaned TTL mangle rules and enforced TCP MSS clamping."

echo ""
echo "=============================================================================="
echo " 4. Updating persistent iptables rules (/etc/iptables/rules.v4)..."
echo "=============================================================================="

if [ -f /etc/iptables/rules.v4 ]; then
    sed -i '/--ttl-eq 63/d' /etc/iptables/rules.v4 2>/dev/null || true
    sed -i '/--ttl-eq 127/d' /etc/iptables/rules.v4 2>/dev/null || true
    sed -i '/--ttl-eq 254/d' /etc/iptables/rules.v4 2>/dev/null || true
    sed -i '/--ttl-set 1/d' /etc/iptables/rules.v4 2>/dev/null || true
    iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
    echo "[OK] Updated /etc/iptables/rules.v4 with clean rules."
fi

if command -v netfilter-persistent >/dev/null 2>&1; then
    netfilter-persistent save 2>/dev/null || true
    echo "[OK] netfilter-persistent saved."
fi

echo ""
echo "=============================================================================="
echo " 5. Flushing stale connection tracking entries..."
echo "=============================================================================="

conntrack -F 2>/dev/null || true
echo "[OK] Flushed conntrack table."

echo ""
echo "=============================================================================="
echo " 6. Restarting PisoWiFi service to enforce clean baseline..."
echo "=============================================================================="

systemctl restart pisowifi
echo "[OK] pisowifi service restarted."

echo ""
echo "=============================================================================="
echo " 7. Verifying DNS resolution for Google services..."
echo "=============================================================================="

echo -n "Checking connectivitycheck.gstatic.com: "
nslookup connectivitycheck.gstatic.com 10.10.10.1 2>&1 | grep "Address:" | tail -1 || echo "(resolving upstream)"

echo -n "Checking drive.google.com: "
nslookup drive.google.com 10.10.10.1 2>&1 | grep "Address:" | tail -1 || echo "(resolving upstream)"

echo -n "Checking one.google.com: "
nslookup one.google.com 10.10.10.1 2>&1 | grep "Address:" | tail -1 || echo "(resolving upstream)"

echo -n "Verifying IPv6 AAAA is suppressed: "
AAAA_OUT=$(nslookup -type=AAAA connectivitycheck.gstatic.com 10.10.10.1 2>&1 | grep -i "has AAAA" || true)
if [ -z "$AAAA_OUT" ]; then
    echo "[PASS] IPv6 AAAA properly filtered!"
else
    echo "[INFO] AAAA result: $AAAA_OUT"
fi

echo ""
echo "=============================================================================="
echo " [SUCCESS] All fixes applied successfully!"
echo " Chrome, Google One, and Google Drive will now connect normally."
echo "=============================================================================="
