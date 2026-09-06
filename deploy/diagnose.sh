#!/usr/bin/env bash
# ==============================================================================
# iConnect - Diagnostic script for usblan0, dnsmasq, and DHCP
# ==============================================================================
# Run as root: sudo bash deploy/diagnose.sh
# ==============================================================================

echo "========================================================"
echo " 1. ALL Network Interfaces (Link & IP)"
echo "========================================================"
ip -br link
echo ""
ip -br addr

echo ""
echo "========================================================"
echo " 2. USB Devices (lsusb)"
echo "========================================================"
if command -v lsusb >/dev/null 2>&1; then
    lsusb
else
    echo "lsusb not installed, checking /sys/bus/usb/devices:"
    ls -l /sys/bus/usb/devices/
fi

echo ""
echo "========================================================"
echo " 3. Recent Kernel Messages (USB & Network)"
echo "========================================================"
dmesg | grep -iE "usb|eth|r815|asix|cdc|dm9|sr9|lan" | tail -n 25

echo ""
echo "========================================================"
echo " 4. Existing udev Rules mentioning 'usblan' or 'NAME='"
echo "========================================================"
grep -rnE "usblan|NAME=" /etc/udev/rules.d/ 2>/dev/null || echo "No custom renaming rules found"

echo ""
echo "========================================================"
echo " 5. dnsmasq Service Status & Test"
echo "========================================================"
echo -n "dnsmasq active: "
systemctl is-active dnsmasq 2>&1 || true
echo "dnsmasq --test result:"
dnsmasq --test 2>&1 || true

echo ""
echo "========================================================"
echo " 6. dnsmasq Configurations"
echo "========================================================"
grep -rnE "bind-interfaces|bind-dynamic|interface=|dhcp-range=" /etc/dnsmasq.conf /etc/dnsmasq.d/ 2>/dev/null || true

echo "========================================================"
