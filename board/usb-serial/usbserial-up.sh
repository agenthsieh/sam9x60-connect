#!/bin/sh
# Bring up the USB-serial (CDC-ACM) gadget on the board.
#
# use_acm=1 makes g_serial present a CDC-ACM interface, which the host's
# cdc-acm driver binds to as /dev/ttyACM0 (vs. the bare "generic serial" that
# needs the host's usbserial driver). This is the USB link for a host that
# lacks the USB-Ethernet-gadget stack (e.g. a Synology NAS: it ships cdc-acm
# but not usbnet/mii).
modprobe g_serial use_acm=1 2>/dev/null
# /dev/ttyGS0 appears once the module is up.
for i in 1 2 3 4 5; do [ -e /dev/ttyGS0 ] && break; sleep 1; done
