#!/usr/bin/env bash
# NetworkManager dispatcher script for PisoWiFi usblan0
IFACE="$1"
ACTION="$2"

if [ "$IFACE" = "usblan0" ]; then
    case "$ACTION" in
        up|dhcp4-change)
            ip link set usblan0 up 2>/dev/null || true
            ip addr replace 10.10.10.1/24 dev usblan0 2>/dev/null || true
            systemctl restart dnsmasq 2>/dev/null || true
            ;;
    esac
fi
