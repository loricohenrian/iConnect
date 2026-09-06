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

echo "=== Step 1: Installing usb-modeswitch (Required for Naxiang 30ae:3300) ==="
apt-get update -qq || true
apt-get install -y -qq usb-modeswitch usb-modeswitch-data || true

# Configure usb_modeswitch for Naxiang adapter
mkdir -p /etc/usb_modeswitch.d
cat << 'EOF' > /etc/usb_modeswitch.d/30ae:3300
# Naxiang SZNX.10/100 Ethernet Adapter
TargetVendor=0x35b5
TargetProduct=0x3500
StandardEject=1
EOF

mkdir -p /etc/udev/rules.d
cat << 'EOF' > /etc/udev/rules.d/40-naxiang-modeswitch.rules
# Auto-switch Naxiang USB adapter from Mass Storage (30ae:3300) to Ethernet (35b5:3500)
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="30ae", ATTR{idProduct}=="3300", RUN+="/usr/sbin/usb_modeswitch -v 30ae -p 3300 -K"
EOF
echo "[OK] usb-modeswitch and udev mode-switch rule configured."

echo "=== Step 2: Configuring NetworkManager static connection for usblan0 ==="
if command -v nmcli >/dev/null 2>&1; then
    for con in $(nmcli -t -f UUID,DEVICE con show 2>/dev/null | grep ":usblan0$" | cut -d: -f1); do
        nmcli con delete "$con" 2>/dev/null || true
    done
    nmcli con delete usblan0 2>/dev/null || true

    nmcli con add type ethernet con-name usblan0 ifname usblan0 \
        ip4 10.10.10.1/24 connection.autoconnect yes \
        connection.autoconnect-priority 100 \
        ipv4.never-default yes 2>/dev/null || true
    echo "[OK] NetworkManager connection configured."
else
    echo "[INFO] NetworkManager (nmcli) not found, skipping nmcli step."
fi

echo "=== Step 3: Installing usblan0-init.service systemd unit ==="
cp "$SCRIPT_DIR/systemd/usblan0-init.service" /etc/systemd/system/usblan0-init.service
chmod 644 /etc/systemd/system/usblan0-init.service
systemctl daemon-reload
systemctl enable usblan0-init.service
echo "[OK] usblan0-init.service enabled."

echo "=== Step 4: Configuring dnsmasq to wait for usblan0 ==="
mkdir -p /etc/systemd/system/dnsmasq.service.d
cp "$SCRIPT_DIR/dnsmasq/wait-for-usblan0.conf" /etc/systemd/system/dnsmasq.service.d/wait-for-usblan0.conf
mkdir -p /etc/dnsmasq.d
cp "$SCRIPT_DIR/dnsmasq/bind-dynamic.conf" /etc/dnsmasq.d/bind-dynamic.conf
echo "[OK] dnsmasq dependencies and dynamic bind configured."

echo "=== Step 5: Installing NetworkManager dispatcher & udev rules ==="
mkdir -p /etc/NetworkManager/dispatcher.d
cp "$SCRIPT_DIR/network/99-usblan0.sh" /etc/NetworkManager/dispatcher.d/99-usblan0.sh
chmod +x /etc/NetworkManager/dispatcher.d/99-usblan0.sh

cp "$SCRIPT_DIR/udev/99-usblan0.rules" /etc/udev/rules.d/99-usblan0.rules
udevadm control --reload-rules 2>/dev/null || true
echo "[OK] NetworkManager dispatcher and udev rules installed."

echo "=== Step 6: Configuring Boot Stability & Kernel Parameters ==="
# Remove old_scheme_first (which breaks 35b5:3500 with error -71)
rm -f /etc/modprobe.d/usbcore.conf

if [ -f /boot/armbianEnv.txt ]; then
    if grep -q "^extraargs=" /boot/armbianEnv.txt; then
        sed -i 's|^extraargs=.*|extraargs=reboot=warm usbcore.initial_descriptor_timeout=5000 usbcore.autosuspend=-1|' /boot/armbianEnv.txt
        echo "[OK] Updated extraargs in /boot/armbianEnv.txt."
    else
        echo "extraargs=reboot=warm usbcore.initial_descriptor_timeout=5000 usbcore.autosuspend=-1" >> /boot/armbianEnv.txt
        echo "[OK] Appended extraargs to /boot/armbianEnv.txt."
    fi
fi

if [ -f /etc/systemd/system.conf ]; then
    sed -i 's/^#*DefaultTimeoutStopSec=.*/DefaultTimeoutStopSec=10s/' /etc/systemd/system.conf
    sed -i 's/^#*DefaultTimeoutAbortSec=.*/DefaultTimeoutAbortSec=10s/' /etc/systemd/system.conf
    echo "[OK] Configured DefaultTimeoutStopSec=10s in /etc/systemd/system.conf."
fi

echo "=== Step 7: Triggering Mode-Switch & Applying Network Settings ==="
# If adapter is currently in Mass Storage mode (30ae:3300), switch it now
if command -v usb_modeswitch >/dev/null 2>&1; then
    usb_modeswitch -v 30ae -p 3300 -K 2>/dev/null || true
    sleep 2
fi

if ip link show usblan0 >/dev/null 2>&1; then
    ip link set usblan0 up 2>/dev/null || true
    ip addr replace 10.10.10.1/24 dev usblan0 2>/dev/null || true
fi
systemctl daemon-reload
systemctl restart dnsmasq

echo ""
echo "=============================================================================="
echo " [SUCCESS] usblan0 mode-switch & boot fix installed successfully!"
echo "=============================================================================="
echo "Interface Status:"
ip -4 addr show usblan0 2>/dev/null || echo "usblan0 not detected yet (may take 2 seconds to switch)"
echo ""
echo "dnsmasq Status:"
systemctl is-active dnsmasq || echo "dnsmasq inactive"
echo "=============================================================================="
echo "Notes:"
echo "1. Naxiang adapter starts as Mass Storage (30ae:3300) on boot."
echo "2. usb-modeswitch will now automatically switch it to Ethernet (35b5:3500)."
echo "3. usblan0-init will assign 10.10.10.1/24 and restart dnsmasq automatically."
echo "=============================================================================="
