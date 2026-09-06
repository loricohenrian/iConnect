#!/usr/bin/env bash
# ==============================================================================
# iConnect - Fix usblan0 Static IP and dnsmasq on Boot
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

echo "=== Step 1: Configuring NetworkManager connection (if available) ==="
if command -v nmcli >/dev/null 2>&1; then
    if nmcli con show usblan0 >/dev/null 2>&1; then
        nmcli con modify usblan0 connection.autoconnect yes connection.autoconnect-priority 100 ipv4.method manual ipv4.addresses 10.10.10.1/24 ipv4.never-default yes 2>/dev/null || true
    else
        nmcli con add type ethernet con-name usblan0 ifname usblan0 ip4 10.10.10.1/24 connection.autoconnect yes connection.autoconnect-priority 100 ipv4.never-default yes 2>/dev/null || true
    fi
fi

echo "=== Step 2: Installing usblan0-init.service systemd unit ==="
cp "$SCRIPT_DIR/systemd/usblan0-init.service" /etc/systemd/system/usblan0-init.service
chmod 644 /etc/systemd/system/usblan0-init.service
systemctl daemon-reload
systemctl enable usblan0-init.service

echo "=== Step 3: Installing NetworkManager dispatcher script ==="
mkdir -p /etc/NetworkManager/dispatcher.d
cp "$SCRIPT_DIR/network/99-usblan0.sh" /etc/NetworkManager/dispatcher.d/99-usblan0.sh
chmod +x /etc/NetworkManager/dispatcher.d/99-usblan0.sh

echo "=== Step 4: Installing updated udev hotplug rule ==="
mkdir -p /etc/udev/rules.d
cp "$SCRIPT_DIR/udev/99-usblan0.rules" /etc/udev/rules.d/99-usblan0.rules
udevadm control --reload-rules 2>/dev/null || true

echo "=== Step 5: Ensuring dnsmasq dynamic bind config exists ==="
mkdir -p /etc/dnsmasq.d
echo "bind-dynamic" > /etc/dnsmasq.d/bind-dynamic.conf

echo "=== Step 6: Initializing usblan0 and restarting dnsmasq now ==="
if ip link show usblan0 >/dev/null 2>&1; then
    ip link set usblan0 up 2>/dev/null || true
    ip addr replace 10.10.10.1/24 dev usblan0 2>/dev/null || true
fi
systemctl restart dnsmasq

echo ""
echo "=============================================================================="
echo " [SUCCESS] usblan0 boot fix installed successfully!"
echo "=============================================================================="
echo "Interface Status:"
ip -4 addr show usblan0 2>/dev/null || echo "usblan0 not detected yet"
echo ""
echo "dnsmasq Status:"
systemctl is-active dnsmasq
echo "=============================================================================="
echo "You can now safely reboot/power-off. On boot, usblan0 will automatically"
echo "get IP 10.10.10.1 and dnsmasq will serve DHCP without needing to replug."
echo "=============================================================================="
