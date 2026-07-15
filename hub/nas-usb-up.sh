#!/bin/sh
# Bring up the board's USB-gadget link on the *host* (e.g. the NAS).
#
# When the board is plugged into a USB port, the host enumerates a CDC-ECM
# network interface. The board is 192.168.9.1; this script gives the host side
# 192.168.9.2 so the hub container can reach the board over USB (no Wi-Fi).
#
# Run as root after plugging in the board:  sudo ./nas-usb-up.sh
# Some hosts (macOS) DHCP the interface automatically and need none of this;
# Synology generally does not, so set it statically.
set -e
HOST_IP=192.168.9.2/24
BOARD=192.168.9.1

# Find the CDC-ECM interface: prefer one whose driver is cdc_ether/cdc_ncm,
# else the newest interface carrying no routable IPv4.
iface=""
for i in $(ls /sys/class/net); do
    drv=$(basename "$(readlink -f /sys/class/net/$i/device/driver 2>/dev/null)" 2>/dev/null)
    case "$drv" in cdc_ether|cdc_ncm|cdc_subset|rndis_host) iface="$i"; break;; esac
done
if [ -z "$iface" ]; then
    echo "No CDC-ECM interface found. Is the board plugged in? Interfaces:" >&2
    ls /sys/class/net >&2
    exit 1
fi
echo "using USB interface: $iface"

ip addr flush dev "$iface" 2>/dev/null || true
ip addr add "$HOST_IP" dev "$iface"
ip link set "$iface" up
echo "host $HOST_IP up on $iface"

if ping -c1 -W2 "$BOARD" >/dev/null 2>&1; then
    echo "OK: board reachable at $BOARD over USB"
else
    echo "WARN: no ping to $BOARD yet — give the board a few seconds, or check its usb0" >&2
fi
