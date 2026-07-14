# board/ — code that runs on the SAM9X60

The connectivity layer (L2) that works today. Everything here runs on the board's
Linux userspace.

| File | What |
|------|------|
| `usbnet-up.sh` | Loads the CDC-ECM gadget (`g_ether`) and assigns `usb0` = `192.168.9.1/24`. |
| `usb_dhcp.py` | A ~50-line DHCP server bound **only** to `usb0`. Hands the host `192.168.9.2` with subnet + lease, but **no gateway/DNS** (so it can't hijack the host's internet). Pure Python stdlib. |
| `usbnet.service` | systemd unit: runs `usbnet-up.sh` then `usb_dhcp.py`, restarts on failure, starts at boot. |

## Requirements on the board

- A Linux kernel with the USB **gadget** stack and the ethernet gadget:
  `CONFIG_USB_GADGET`, `CONFIG_USB_ETH` (`g_ether`), and a working device-mode
  USB controller (on the SAM9X60 that's the `atmel_usba_udc`, i.e. the USB
  **device** port — not a host port).
- `python3` (stdlib only).

## Notes

- The device port on the SAM9X60 is the one wired to the SoC's USB device
  controller. On some boards its VBUS-detect pin differs from the reference
  design — if the gadget never enumerates (`/sys/class/udc/*/state` stays
  `not attached`), check the `atmel,vbus-gpio` in your device tree.
- The `192.168.9.0/24` subnet is arbitrary; change it in `usbnet-up.sh` and
  `usb_dhcp.py` if it clashes with your network.

*Coming next:* the OTA agent (SWUpdate client + A/B slot switching) lands here for
milestone **M1**.
