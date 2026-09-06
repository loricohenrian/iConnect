#!/usr/bin/env bash
# ==============================================================================
# iConnect - Fix usblan0 Static IP, dnsmasq, and Orange Pi Reboot Freeze
# ==============================================================================
# Run as root: sudo bash deploy/fix_usblan0_boot.sh
# ==============================================================================

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] This script must be run as root (use: sudo bash deploy/fix_usblan0_boot.sh)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Step 1: Configuring NetworkManager static connection for usblan0 ==="
if command -v nmcli >/dev/null 2>&1; then
    # Remove any conflicting/auto connections assigned to usblan0
    for con in $(nmcli -t -f UUID,DEVICE con show 2>/dev/null | grep ":usblan0$" | cut -d: -f1); do
        nmcli con delete "$con" 2>/dev/null || true
    done
    nmcli con delete usblan0 2>/dev/null || true

    # Add dedicated static profile with never-default route
    nmcli con add type ethernet con-name usblan0 ifname usblan0 \
        ip4 10.10.10.1/24 connection.autoconnect yes \
        connection.autoconnect-priority 100 \
        ipv4.never-default yes 2>/dev/null || true
    echo "[OK] NetworkManager connection configured."
else
    echo "[INFO] NetworkManager (nmcli) not found, skipping nmcli step."
fi

echo "=== Step 2: Installing usblan0-init.service systemd unit ==="
cp "$SCRIPT_DIR/systemd/usblan0-init.service" /etc/systemd/system/usblan0-init.service
chmod 644 /etc/systemd/system/usblan0-init.service
systemctl daemon-reload
systemctl enable usblan0-init.service
echo "[OK] usblan0-init.service enabled."

echo "=== Step 3: Configuring dnsmasq to wait for usblan0 ==="
mkdir -p /etc/systemd/system/dnsmasq.service.d
cp "$SCRIPT_DIR/dnsmasq/wait-for-usblan0.conf" /etc/systemd/system/dnsmasq.service.d/wait-for-usblan0.conf
mkdir -p /etc/dnsmasq.d
cp "$SCRIPT_DIR/dnsmasq/bind-dynamic.conf" /etc/dnsmasq.d/bind-dynamic.conf
echo "[OK] dnsmasq dependencies and dynamic bind configured."

echo "=== Step 4: Installing NetworkManager dispatcher script ==="
mkdir -p /etc/NetworkManager/dispatcher.d
cp "$SCRIPT_DIR/network/99-usblan0.sh" /etc/NetworkManager/dispatcher.d/99-usblan0.sh
chmod +x /etc/NetworkManager/dispatcher.d/99-usblan0.sh
echo "[OK] NetworkManager dispatcher installed."

echo "=== Step 5: Installing updated udev hotplug rule ==="
mkdir -p /etc/udev/rules.d
cp "$SCRIPT_DIR/udev/99-usblan0.rules" /etc/udev/rules.d/99-usblan0.rules
udevadm control --reload-rules 2>/dev/null || true
echo "[OK] udev rule installed."

echo "=== Step 6: Preventing Orange Pi One reboot freeze (H3 SoC watchdog fix) ==="
if [ -f /boot/armbianEnv.txt ]; then
    if grep -q "^extraargs=" /boot/armbianEnv.txt; then
        if ! grep -q "reboot=" /boot/armbianEnv.txt; then
            sed -i 's/^extraargs=\(.*\)/extraargs=\1 reboot=warm/' /boot/armbianEnv.txt
            echo "[OK] Added reboot=warm to extraargs in /boot/armbianEnv.txt."
        else
            echo "[OK] reboot parameter already present in /boot/armbianEnv.txt."
        fi
    else
        echo "extraargs=reboot=warm" >> /boot/armbianEnv.txt
        echo "[OK] Appended extraargs=reboot=warm to /boot/armbianEnv.txt."
    fi
else
    echo "[INFO] /boot/armbianEnv.txt not found (non-Armbian or custom image)."
fi

# Ensure systemd never hangs on shutdown/reboot waiting for slow processes
if [ -f /etc/systemd/system.conf ]; then
    sed -i 's/^#*DefaultTimeoutStopSec=.*/DefaultTimeoutStopSec=10s/' /etc/systemd/system.conf
    sed -i 's/^#*DefaultTimeoutAbortSec=.*/DefaultTimeoutAbortSec=10s/' /etc/systemd/system.conf
    echo "[OK] Configured DefaultTimeoutStopSec=10s in /etc/systemd/system.conf."
fi

echo "=== Step 7: Applying network settings and restarting dnsmasq now ==="
if ip link show usblan0 >/dev/null 2>&1; then
    ip link set usblan0 up 2>/dev/null || true
    ip addr replace 10.10.10.1/24 dev usblan0 2>/dev/null || true
fi
systemctl daemon-reload
systemctl restart dnsmasq

echo ""
echo "=============================================================================="
echo " [SUCCESS] usblan0 boot fix & reboot fix installed successfully!"
echo "=============================================================================="
echo "Interface Status:"
ip -4 addr show usblan0 2>/dev/null || echo "usblan0 not detected yet"
echo ""
echo "dnsmasq Status:"
systemctl is-active dnsmasq || echo "dnsmasq inactive"
echo "=============================================================================="
echo "Notes:"
echo "1. On cold boot or reboot, usblan0 will now automatically get 10.10.10.1"
echo "   and dnsmasq will start right after without needing to unplug/replug."
echo "2. The Allwinner H3 reboot hang has been fixed with reboot=warm in armbianEnv.txt."
echo "=============================================================================="
