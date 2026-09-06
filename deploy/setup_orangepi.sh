#!/usr/bin/env bash
# ==============================================================================
# iConnect - Automated Orange Pi (One / Zero 3) Linux Setup Script
# ==============================================================================
# Run as root: sudo bash deploy/setup_orangepi.sh
# ==============================================================================

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] This script must be run as root (use: sudo bash deploy/setup_orangepi.sh)"
    exit 1
fi

PROJECT_ROOT="${PROJECT_ROOT:-/opt/iconnect/pisowifi}"

if [ ! -d "$PROJECT_ROOT" ]; then
    echo "[ERROR] Project directory $PROJECT_ROOT not found."
    echo "Please clone repository to $PROJECT_ROOT or set PROJECT_ROOT env var."
    exit 1
fi

echo "=== 1/7: Installing system packages ==="
apt-get update
apt-get install -y python3-venv python3-pip nginx redis-server dnsmasq iptables conntrack curl

echo "=== 2/7: Configuring dnsmasq for USB-to-LAN adapter ==="
mkdir -p /etc/dnsmasq.d
cp "$PROJECT_ROOT/deploy/dnsmasq/bind-dynamic.conf" /etc/dnsmasq.d/bind-dynamic.conf

mkdir -p /etc/systemd/system/dnsmasq.service.d
cp "$PROJECT_ROOT/deploy/dnsmasq/wait-for-usblan0.conf" /etc/systemd/system/dnsmasq.service.d/wait-for-usblan0.conf

# Silence cosmetic resolvconf warning on Armbian / systemd-resolved
if [ -f /etc/default/dnsmasq ]; then
    if ! grep -q "IGNORE_RESOLVCONF=yes" /etc/default/dnsmasq; then
        echo "IGNORE_RESOLVCONF=yes" >> /etc/default/dnsmasq
    fi
fi

echo "=== 3/7: Installing udev rules and network dispatcher for usblan0 ==="
cp "$PROJECT_ROOT/deploy/udev/99-usblan0.rules" /etc/udev/rules.d/99-usblan0.rules
udevadm control --reload-rules 2>/dev/null || true

mkdir -p /etc/NetworkManager/dispatcher.d
if [ -f "$PROJECT_ROOT/deploy/network/99-usblan0.sh" ]; then
    cp "$PROJECT_ROOT/deploy/network/99-usblan0.sh" /etc/NetworkManager/dispatcher.d/99-usblan0.sh
    chmod +x /etc/NetworkManager/dispatcher.d/99-usblan0.sh
fi

echo "=== 4/7: Installing systemd services ==="
cp "$PROJECT_ROOT/deploy/systemd/usblan0-init.service" /etc/systemd/system/
cp "$PROJECT_ROOT/deploy/systemd/pisowifi.service" /etc/systemd/system/
cp "$PROJECT_ROOT/deploy/systemd/coindetector.service" /etc/systemd/system/
cp "$PROJECT_ROOT/deploy/systemd/celery-worker.service" /etc/systemd/system/
cp "$PROJECT_ROOT/deploy/systemd/celery-beat.service" /etc/systemd/system/
systemctl daemon-reload

echo "=== 5/8: Configuring Orange Pi One stability settings ==="
mkdir -p /etc/modprobe.d
cat << 'EOF' > /etc/modprobe.d/usbcore.conf
options usbcore old_scheme_first=1 use_both_schemes=1 initial_descriptor_timeout=5000 autosuspend=-1
EOF

if [ -f /boot/armbianEnv.txt ]; then
    if grep -q "^extraargs=" /boot/armbianEnv.txt; then
        sed -i 's|^extraargs=.*|extraargs=reboot=warm usbcore.old_scheme_first=1 usbcore.use_both_schemes=1 usbcore.initial_descriptor_timeout=5000 usbcore.autosuspend=-1|' /boot/armbianEnv.txt
    else
        echo "extraargs=reboot=warm usbcore.old_scheme_first=1 usbcore.use_both_schemes=1 usbcore.initial_descriptor_timeout=5000 usbcore.autosuspend=-1" >> /boot/armbianEnv.txt
    fi
fi
if [ -f /etc/systemd/system.conf ]; then
    sed -i 's/^#*DefaultTimeoutStopSec=.*/DefaultTimeoutStopSec=10s/' /etc/systemd/system.conf
    sed -i 's/^#*DefaultTimeoutAbortSec=.*/DefaultTimeoutAbortSec=10s/' /etc/systemd/system.conf
fi

echo "=== 6/8: Configuring Nginx ==="
cp "$PROJECT_ROOT/deploy/nginx/iconnect.conf" /etc/nginx/sites-available/iconnect.conf
ln -sf /etc/nginx/sites-available/iconnect.conf /etc/nginx/sites-enabled/iconnect.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t

echo "=== 7/8: Enabling all services ==="
systemctl enable redis-server dnsmasq nginx usblan0-init pisowifi coindetector celery-worker celery-beat

echo "=== 8/8: Restarting services ==="
systemctl restart redis-server dnsmasq nginx
systemctl restart pisowifi coindetector celery-worker celery-beat || true

echo ""
echo "=============================================================================="
echo " [SUCCESS] Orange Pi Linux setup completed successfully!"
echo "=============================================================================="
echo "- dnsmasq is configured to wait for and dynamically bind to usblan0."
echo "- Nginx, Gunicorn, Celery, and Coin Detector services are installed and enabled."
echo "=============================================================================="
