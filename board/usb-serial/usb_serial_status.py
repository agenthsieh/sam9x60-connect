#!/usr/bin/env python3
"""Emit board status as newline-delimited JSON on the USB-serial gadget.

The board presents a CDC-ACM gadget (/dev/ttyGS0 here, /dev/ttyACM0 on the
host). This is the USB data path for hosts that can't do a USB-Ethernet gadget
(a Synology NAS has cdc-acm but not the usbnet/mii stack). We reuse the board's
existing HTTP API on localhost so there's one source of truth for sensor data.

Telemetry only, board -> host: one compact JSON object per line, every INTERVAL
seconds. Writes are non-blocking, so nothing stalls when no host is reading.
"""
import os, time, json, errno, urllib.request

PORT     = os.environ.get("GS_PORT", "/dev/ttyGS0")
INTERVAL = float(os.environ.get("GS_INTERVAL", "2"))
API      = "http://127.0.0.1:8080"


def get(path):
    try:
        with urllib.request.urlopen(API + path, timeout=2) as r:
            if r.status == 200:
                return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        pass
    return None


def open_port():
    # O_RDWR so the gadget's carrier logic is happy; O_NONBLOCK so writes drop
    # instead of blocking when the host isn't draining the tty.
    while True:
        try:
            return os.open(PORT, os.O_RDWR | os.O_NONBLOCK | os.O_NOCTTY)
        except OSError:
            time.sleep(1)


def main():
    fd = open_port()
    while True:
        line = json.dumps({
            "ts": int(time.time()),
            "sysinfo": get("/api/sysinfo") or {},
            "hr": get("/api/hr/data"),
        }, separators=(",", ":")) + "\n"
        try:
            os.write(fd, line.encode())
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                pass                    # host not reading; skip this sample
            elif e.errno in (errno.EIO, errno.ENODEV, errno.ENXIO):
                os.close(fd); fd = open_port()   # gadget re-enumerated
            else:
                raise
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
