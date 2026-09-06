#!/usr/bin/env bash
# ==============================================================================
# iConnect - Software USB Bus Reset Test
# ==============================================================================
# Run as root: sudo bash deploy/test_usb_reset.sh
# ==============================================================================

set -u

echo "========================================================"
echo " 1. USB & Network status BEFORE reset"
echo "========================================================"
lsusb 2>&1 || true
ip -br link

echo ""
echo "========================================================"
echo " 2. Resetting EHCI USB controllers via sysfs..."
echo "========================================================"
found=0
for ctrl in /sys/bus/platform/drivers/ehci-platform/*.usb; do
    if [ -d "$ctrl" ]; then
        found=1
        name=$(basename "$ctrl")
        echo "Resetting controller $name..."
        echo "$name" > /sys/bus/platform/drivers/ehci-platform/unbind 2>/dev/null || true
        sleep 1
        echo "$name" > /sys/bus/platform/drivers/ehci-platform/bind 2>/dev/null || true
    fi
done

if [ "$found" -eq 0 ]; then
    echo "No *.usb entries in ehci-platform, checking all entries:"
    ls -l /sys/bus/platform/drivers/ehci-platform/
fi

echo ""
echo "Waiting 4 seconds for USB enumeration..."
sleep 4

echo "========================================================"
echo " 3. USB & Network status AFTER reset"
echo "========================================================"
lsusb 2>&1 || true
echo ""
ip -br link
echo ""
ip -br addr
echo "========================================================"
