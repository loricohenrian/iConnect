#!/usr/bin/env bash
# ==============================================================================
# iConnect - Setup / Switch to UGREEN (or any new) USB-to-LAN Gigabit Adapter
# ==============================================================================
# Run as root: sudo bash deploy/setup_ugreen_adapter.sh
# ==============================================================================

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] This script must be run as root: sudo bash deploy/setup_ugreen_adapter.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=============================================================================="
echo " iConnect - Configuring USB-to-LAN Adapter (UGREEN / AX88179 / RTL8153)"
echo "=============================================================================="

# 1. Install systemd network link rule (names ANY USB Ethernet adapter 'usblan0')
mkdir -p /etc/systemd/network
if [ -f "$SCRIPT_DIR/network/10-usblan.link" ]; then
    cp "$SCRIPT_DIR/network/10-usblan.link" /etc/systemd/network/10-usblan.link
else
    cat << 'EOF' > /etc/systemd/network/10-usblan.link
[Match]
Type=ether
Path=*-usb-*

[Link]
Name=usblan0
EOF
fi
echo "[OK] Installed /etc/systemd/network/10-usblan.link"

# 2. Install udev renaming rule
mkdir -p /etc/udev/rules.d
cat << 'EOF' > /etc/udev/rules.d/70-persistent-net-usblan.rules
# Automatically assign 'usblan0' to any USB Ethernet adapter
SUBSYSTEM=="net", ACTION=="add", DEVPATH=="*/usb*/*", NAME="usblan0"
EOF
echo "[OK] Installed /etc/udev/rules.d/70-persistent-net-usblan.rules"

# Reload udev rules
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger --subsystem-match=net 2>/dev/null || true

# 3. Configure NetworkManager connection for usblan0
if command -v nmcli >/dev/null 2>&1; then
    echo "[*] Configuring NetworkManager connection..."
    for con in $(nmcli -t -f UUID,DEVICE con show 2>/dev/null | grep ":usblan0$" | cut -d: -f1); do
        nmcli con delete "$con" 2>/dev/null || true
    done
    nmcli con delete usblan0 2>/dev/null || true

    nmcli con add type ethernet con-name usblan0 ifname usblan0 \
        ip4 10.10.10.1/24 connection.autoconnect yes \
        connection.autoconnect-priority 100 \
        ipv4.never-default yes 2>/dev/null || true
    echo "[OK] NetworkManager profile 'usblan0' configured."
fi

# 4. Bring up interface if already connected
sleep 1
if ip link show usblan0 >/dev/null 2>&1; then
    ip link set usblan0 up 2>/dev/null || true
    ip addr replace 10.10.10.1/24 dev usblan0 2>/dev/null || true
    echo "[OK] Interface usblan0 is UP with IP 10.10.10.1/24"
else
    USB_DEV=$(ls -d /sys/bus/usb/devices/*/net/* 2>/dev/null | head -n1 | xargs -n1 basename 2>/dev/null || true)
    if [ -n "$USB_DEV" ] && [ "$USB_DEV" != "usblan0" ]; then
        echo "[INFO] Detected USB network device '$USB_DEV', binding 10.10.10.1/24..."
        ip link set "$USB_DEV" up 2>/dev/null || true
        ip addr replace 10.10.10.1/24 dev "$USB_DEV" 2>/dev/null || true
    fi
fi

# 5. Restart dnsmasq and iConnect services
systemctl restart dnsmasq 2>/dev/null || true
systemctl restart pisowifi 2>/dev/null || true

echo ""
echo "=============================================================================="
echo " [SUCCESS] USB-to-LAN configuration complete!"
echo "=============================================================================="
echo "Network status:"
ip -br addr show dev usblan0 2>/dev/null || ip -br addr
echo ""
echo "dnsmasq status:"
systemctl is-active dnsmasq 2>/dev/null && echo "dnsmasq is ACTIVE" || echo "dnsmasq inactive"
echo "=============================================================================="
