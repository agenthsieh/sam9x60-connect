#!/usr/bin/env python3
"""Bidirectional USB-serial agent on the board's CDC-ACM gadget.

Supersedes usb_serial_status.py: as well as streaming status telemetry to the
host, it accepts a file push (e.g. a .swu) and can apply it with SWUpdate — all
over the one CDC-ACM link, so nothing touches the network.

Wire protocol on /dev/ttyGS0 — newline-delimited JSON control messages, each
optionally followed by a fixed number of raw bytes. USB bulk transfer is
reliable and in-order, so there's no per-chunk retransmit; a final CRC32 catches
corruption.

  host -> board:
    {"t":"put","name":"<f>","size":<S>,"crc":<crc32>}  then exactly <S> raw bytes
    {"t":"apply","name":"<f>"}                          apply <f> via swupdate
    {"t":"ping"}
  board -> host:
    {"t":"tel","sysinfo":{…},"hr":{…}}                  telemetry, every INTERVAL
    {"t":"progress","recv":<X>,"size":<S>}              during a receive
    {"t":"put_result","ok":<bool>,"name":"<f>","crc":<c>,"err":"…"}
    {"t":"apply_result","ok":<bool>,"msg":"…"}
    {"t":"pong"}
"""
import os, sys, json, time, zlib, errno, termios, threading, subprocess, urllib.request

PORT     = os.environ.get("GS_PORT", "/dev/ttyGS0")
INTERVAL = float(os.environ.get("GS_INTERVAL", "2"))
SPOOL    = os.environ.get("GS_SPOOL", "/tmp/ota")
API      = "http://127.0.0.1:8080"

_wlock = threading.Lock()


def api_get(path):
    try:
        with urllib.request.urlopen(API + path, timeout=2) as r:
            if r.status == 200:
                return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def open_port():
    while True:
        try:
            fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY)
            a = termios.tcgetattr(fd)
            a[0] = a[1] = a[3] = 0
            a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            termios.tcsetattr(fd, termios.TCSANOW, a)
            return fd
        except OSError:
            time.sleep(1)


def send(fd, obj):
    line = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
    with _wlock:
        try:
            os.write(fd, line)
        except OSError:
            pass


def telemetry(fd, stop):
    while not stop.is_set():
        send(fd, {"t": "tel", "sysinfo": api_get("/api/sysinfo") or {},
                  "hr": api_get("/api/hr/data")})
        stop.wait(INTERVAL)


def apply_swu(fd, path):
    if not os.path.exists(path):
        return send(fd, {"t": "apply_result", "ok": False, "msg": "no such file"})
    try:
        p = subprocess.run(["swupdate", "-i", path], capture_output=True,
                           text=True, timeout=600)
        tail = (p.stdout + p.stderr).strip().splitlines()[-1:] or [""]
        send(fd, {"t": "apply_result", "ok": p.returncode == 0, "msg": tail[0][:200]})
    except Exception as e:
        send(fd, {"t": "apply_result", "ok": False, "msg": str(e)[:200]})


def reader(fd):
    os.makedirs(SPOOL, exist_ok=True)
    buf = b""
    mode = "line"
    need = 0; fout = None; crc = 0; got = 0; ctx = None; last_prog = 0
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError as e:
            if e.errno in (errno.EIO, errno.ENODEV, errno.ENXIO):
                return                          # gadget went away; main reopens
            time.sleep(0.2); continue
        if not chunk:
            continue
        buf += chunk
        while buf:
            if mode == "line":
                nl = buf.find(b"\n")
                if nl < 0:
                    break
                raw, buf = buf[:nl], buf[nl + 1:]
                try:
                    msg = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                t = msg.get("t")
                if t == "ping":
                    send(fd, {"t": "pong"})
                elif t == "apply":
                    apply_swu(fd, os.path.join(SPOOL, os.path.basename(msg.get("name", ""))))
                elif t == "put":
                    ctx = {"name": os.path.basename(msg.get("name", "upload.bin")),
                           "size": int(msg.get("size", 0)), "crc": msg.get("crc")}
                    fout = open(os.path.join(SPOOL, ctx["name"]), "wb")
                    crc = 0; got = 0; last_prog = 0
                    need = ctx["size"]; mode = "data" if need > 0 else "line"
                    if need == 0:
                        fout.close(); send(fd, {"t": "put_result", "ok": True,
                                                "name": ctx["name"], "crc": 0})
            else:  # mode == "data": consume exactly `need` bytes
                take = min(need, len(buf))
                fout.write(buf[:take]); crc = zlib.crc32(buf[:take], crc)
                got += take; need -= take; buf = buf[take:]
                if got - last_prog >= 262144 or need == 0:
                    last_prog = got
                    send(fd, {"t": "progress", "recv": got, "size": ctx["size"]})
                if need == 0:
                    fout.close(); fout = None; mode = "line"
                    ok = (ctx["crc"] is None) or (crc == ctx["crc"])
                    send(fd, {"t": "put_result", "ok": ok, "name": ctx["name"],
                              "crc": crc, "err": None if ok else "crc mismatch"})


def main():
    while True:
        fd = open_port()
        stop = threading.Event()
        threading.Thread(target=telemetry, args=(fd, stop), daemon=True).start()
        reader(fd)                              # returns if the gadget drops
        stop.set()
        try: os.close(fd)
        except OSError: pass
        time.sleep(1)


if __name__ == "__main__":
    main()
