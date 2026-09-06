#!/usr/bin/env bash
# ==============================================================================
# iConnect - Diagnostic script for usblan0, dnsmasq, and DHCP
# ==============================================================================
# Run as root: sudo bash deploy/diagnose.sh
# ==============================================================================

echo "========================================================"
echo " 1. IP & Interface Status for usblan0"
echo "========================================================"
ip -d link show usblan0 2>&1 || echo "usblan0 device not found"
ip -4 addr show usblan0 2>&1 || echo "usblan0 has no IPv4"

echo ""
echo "========================================================"
echo " 2. dnsmasq Syntax Test & Active Status"
echo "========================================================"
echo -n "dnsmasq active: "
systemctl is-active dnsmasq 2>&1 || true
echo "dnsmasq --test result:"
dnsmasq --test 2>&1 || true

echo ""
echo "========================================================"
echo " 3. dnsmasq Conf Analysis (bind options & dhcp)"
echo "========================================================"
grep -rnE "bind-interfaces|bind-dynamic|interface=|dhcp-range=" /etc/dnsmasq.conf /etc/dnsmasq.d/ 2>/dev/null || true

echo ""
echo "========================================================"
echo " 4. usblan0-init Service Status"
echo "========================================================"
systemctl status usblan0-init.service --no-pager 2>&1 || true

echo ""
echo "========================================================"
echo " 5. Recent dnsmasq logs (this boot)"
echo "========================================================"
journalctl -u dnsmasq -b --no-pager -n 25 2>&1 || true

echo ""
echo "========================================================"
echo " 6. NetworkManager Device Status"
echo "========================================================"
if command -v nmcli >/dev/null 2>&1; then
    nmcli device status 2>&1 || true
else
    echo "nmcli not installed"
fi
echo "========================================================"
