#!/bin/sh
# Bring up the USB gadget ethernet link on the SAM9X60.
#   - loads the CDC-ECM ethernet gadget (g_ether)
#   - waits for the usb0 interface to appear
#   - assigns the board's on-board address (192.168.9.1/24)
# The DHCP server (usb_dhcp.py, started by usbnet.service) then hands the host
# 192.168.9.2 with NO gateway/DNS, so it never hijacks the host's networking.

modprobe g_ether 2>/dev/null
for i in 1 2 3 4 5; do
    ip link show usb0 >/dev/null 2>&1 && break
    sleep 1
done
ip addr add 192.168.9.1/24 dev usb0 2>/dev/null
ip link set usb0 up
