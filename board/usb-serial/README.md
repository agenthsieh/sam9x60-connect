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
| [`usb_serial_agent.py`](usb_serial_agent.py) | **bidirectional** agent: streams telemetry out *and* accepts a file push (e.g. a `.swu`) + applies it with SWUpdate |
| [`usb_serial_status.py`](usb_serial_status.py) | telemetry-only predecessor (kept for reference) |
| [`usbserial.service`](usbserial.service) | systemd unit (runs the agent); `Conflicts=usbnet.service` |

## Push protocol

Newline-delimited JSON control messages, each optionally followed by a fixed
number of raw bytes. USB bulk transfer is reliable and in-order, so there's no
per-chunk retransmit — a final CRC32 catches corruption.

```
host → board  {"t":"put","name":"x.swu","size":S,"crc":C}  + S raw bytes
              {"t":"apply","name":"x.swu"}
board → host  {"t":"tel",…}            telemetry (every 2 s)
              {"t":"progress","recv":X,"size":S}
              {"t":"put_result","ok":bool,"crc":C}
              {"t":"apply_result","ok":bool,"msg":"…"}
```

The received file lands in `GS_SPOOL` (default `/tmp/ota`); `apply` runs
`swupdate -i <file>`, so a real `.swu` writes the inactive A/B slot and arms the
switch (see [`../ota/`](../ota/)).

## Install (board)

```sh
cp usb_serial_agent.py /root/
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
