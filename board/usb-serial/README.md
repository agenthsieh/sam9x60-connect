# board/usb-serial — USB-serial telemetry link

The board's USB data path for hosts that can't do a USB-Ethernet gadget. It
presents a **CDC-ACM serial gadget** and streams JSON status lines to the host,
which the [NAS hub](../../hub/README.md) reads from `/dev/ttyACM0`.

## Why serial

A USB-Ethernet gadget (CDC-ECM/RNDIS) is nicer — the host could just HTTP-poll
the board over USB — but it needs `usbnet` (+`mii`) on the host. A Synology NAS
ships `cdc-acm` but **not** the usbnet/mii stack, so the Ethernet gadget never
gets a network interface there. A serial gadget only needs `cdc-acm`, which the
NAS has. So: board data stays off Wi-Fi, over a link every host supports.

## Pieces

| file | role |
|------|------|
| [`usbserial-up.sh`](usbserial-up.sh) | `modprobe g_serial use_acm=1` → `/dev/ttyGS0`, a CDC-ACM gadget |
| [`usb_serial_status.py`](usb_serial_status.py) | every 2 s, write `{"ts","sysinfo","hr"}` to `/dev/ttyGS0` (non-blocking; reuses the board's own `:8080` API) |
| [`usbserial.service`](usbserial.service) | systemd unit; `Conflicts=usbnet.service` |

## Install (board)

```sh
cp usb_serial_status.py /root/
cp usbserial-up.sh /usr/local/sbin/ && chmod +x /usr/local/sbin/usbserial-up.sh
cp usbserial.service /etc/systemd/system/
systemctl disable --now usbnet          # release the g_ether gadget
rmmod g_ether
systemctl enable --now usbserial
```

`use_acm=1` matters: without it `g_serial` presents a bare bulk-serial that
needs the host's generic `usbserial` driver; with it, it's CDC-ACM →
`/dev/ttyACM0` via the host's stock `cdc-acm`.

## Notes

- One writer only: stop `usbserial` before using `/dev/ttyGS0` for anything else.
- Telemetry is one-way (board → host). Commands back to the board can share the
  same tty later with a small request/response framing.
- Composite gadget (ECM *and* ACM at once) is possible via configfs if you also
  want the Ethernet path on capable hosts — not needed for the NAS.
