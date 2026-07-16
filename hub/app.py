#!/usr/bin/env python3
"""SAM9X60 Connect — NAS Hub.

A small management console that runs as a Docker container (e.g. on a Synology
NAS) and watches a SAM9X60 board over the LAN. It polls the board's on-device
web API and derives a connectivity state, so the dashboard can show live status
when the board is up, guidance to push a recovery image when the QSPI installer
is running, and "insert SD / check power" guidance when the board is dark.

Zero third-party dependencies — Python stdlib only (same http.server stack the
board itself uses), so the container image is tiny and needs no pip install.

Two transports:
  * USB serial (preferred) — the board streams JSON telemetry over a CDC-ACM
    gadget; set SERIAL_DEV=/dev/ttyACM0. Keeps board data off Wi-Fi even on a
    host (e.g. a Synology NAS) whose kernel can't do a USB-Ethernet gadget.
  * HTTP poll — set BOARD_HOST to the board's IP; the hub polls its web API.

Config via env:
  SERIAL_DEV      CDC-ACM device e.g. /dev/ttyACM0  (enables serial mode)
  BOARD_HOST      board IP / hostname               (HTTP mode)
  BOARD_PORT      on-device web console port        (default 8080)
  INSTALLER_PORT  recovery SWUpdate port            (default 8090)
  POLL_INTERVAL   seconds between board polls        (default 3)
  STALE_AFTER     seconds without data -> offline    (default 8)
  HUB_PORT        port this hub listens on           (default 8091)
"""
import os, json, time, zlib, threading, socketserver, termios, urllib.request, urllib.error
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERIAL_DEV     = os.environ.get("SERIAL_DEV", "").strip()
BOARD_HOST     = os.environ.get("BOARD_HOST", "").strip()
BOARD_PORT     = int(os.environ.get("BOARD_PORT", "8080"))
INSTALLER_PORT = int(os.environ.get("INSTALLER_PORT", "8090"))
POLL_INTERVAL  = float(os.environ.get("POLL_INTERVAL", "3"))
STALE_AFTER    = float(os.environ.get("STALE_AFTER", "8"))
HUB_PORT       = int(os.environ.get("HUB_PORT", "8091"))

_state = {
    "conn": "unknown",     # ready | installer | offline
    "sysinfo": None,       # last /api/sysinfo payload
    "hr": None,            # last /api/hr/data payload
    "last_ok": 0,
    "checked": 0,
    "board": {"transport": "serial" if SERIAL_DEV else "http",
              "source": SERIAL_DEV or f"{BOARD_HOST}:{BOARD_PORT}",
              "host": BOARD_HOST, "port": BOARD_PORT},
}
_lock = threading.Lock()

# rolling telemetry history for the dashboard chart (~10 min at a 2 s cadence)
HISTORY_MAX = int(os.environ.get("HISTORY_MAX", "300"))
_history = deque(maxlen=HISTORY_MAX)


def _record(sysinfo, hr):
    """Append one point to the history ring (called on each fresh reading)."""
    s = sysinfo or {}
    pt = {"ts": int(time.time()), "temp": s.get("temp"), "load1": s.get("load1"),
          "bpm": (hr or {}).get("bpm")}
    with _lock:
        # de-dupe identical timestamps (board may repeat within a second)
        if not _history or _history[-1]["ts"] != pt["ts"]:
            _history.append(pt)


def _get(url, timeout=2.5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status == 200:
                return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    return None


def _probe_installer():
    # The recovery SWUpdate server answers on INSTALLER_PORT; any non-5xx (or
    # even an HTTPError) means something is listening = installer is up.
    try:
        with urllib.request.urlopen(f"http://{BOARD_HOST}:{INSTALLER_PORT}/", timeout=2.0) as r:
            return r.status < 500
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def poll_loop():
    while True:
        sysinfo = _get(f"http://{BOARD_HOST}:{BOARD_PORT}/api/sysinfo") if BOARD_HOST else None
        now = time.time()
        with _lock:
            _state["checked"] = now
            if sysinfo:
                hr = _get(f"http://{BOARD_HOST}:{BOARD_PORT}/api/hr/data")
                _state.update(conn="ready", sysinfo=sysinfo, hr=hr, last_ok=now)
            elif BOARD_HOST and _probe_installer():
                _state.update(conn="installer", sysinfo=None, hr=None)
            else:
                _state.update(conn="offline", sysinfo=None, hr=None)
        if sysinfo:
            _record(sysinfo, hr)
        time.sleep(POLL_INTERVAL)


# --- shared CDC-ACM fd (one reader loop + the OTA sender write to it) --------
_ser_fd = None
_ser_wlock = threading.Lock()
_ota = {"active": False, "name": None, "size": 0, "sent": 0, "recv": 0,
        "phase": "idle", "ok": None, "msg": ""}   # phase: idle|send|apply|done|error


def _ser_write(obj):
    fd = _ser_fd
    if fd is None:
        raise OSError("serial not open")
    with _ser_wlock:
        os.write(fd, (json.dumps(obj, separators=(",", ":")) + "\n").encode())


def _ser_write_raw(data):
    fd = _ser_fd
    if fd is None:
        raise OSError("serial not open")
    with _ser_wlock:
        mv = memoryview(data)
        off = 0
        while off < len(mv):
            off += os.write(fd, mv[off:])       # blocks on USB flow control


def _dispatch(obj):
    t = obj.get("t")
    if t in (None, "tel"):                       # telemetry (tagged or bare)
        now = time.time()
        with _lock:
            _state.update(conn="ready", sysinfo=obj.get("sysinfo"),
                          hr=obj.get("hr"), last_ok=now, checked=now)
        _record(obj.get("sysinfo"), obj.get("hr"))
    elif t == "inst":                            # recovery installer heartbeat
        now = time.time()
        with _lock:
            _state.update(conn="installer", sysinfo=None, hr=None,
                          last_ok=now, checked=now,
                          installer={k: v for k, v in obj.items() if k != "t"})
    elif t == "progress":
        with _lock:
            _ota["recv"] = obj.get("recv", 0)
    elif t == "put_result":
        with _lock:
            _ota["recv"] = _ota["size"] if obj.get("ok") else _ota["recv"]
            if not obj.get("ok"):
                _ota.update(phase="error", ok=False, active=False,
                            msg=obj.get("err") or "put failed")
            _ota["_put_ok"] = bool(obj.get("ok"))
    elif t == "apply_result":
        with _lock:
            _ota.update(phase="done" if obj.get("ok") else "error",
                        ok=bool(obj.get("ok")), active=False,
                        msg=obj.get("msg", ""))


def serial_loop():
    """Own the CDC-ACM fd: raw-read and dispatch newline-delimited JSON."""
    global _ser_fd
    while True:
        try:
            fd = os.open(SERIAL_DEV, os.O_RDWR | os.O_NOCTTY)
            try:
                a = termios.tcgetattr(fd)
                a[0] = a[1] = a[3] = 0           # raw: no in/out/local processing
                a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
                termios.tcsetattr(fd, termios.TCSANOW, a)
            except Exception:
                pass
            _ser_fd = fd
            buf = b""
            while True:
                chunk = os.read(fd, 4096)
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    try:
                        _dispatch(json.loads(raw.decode("utf-8", "replace")))
                    except ValueError:
                        continue
                if len(buf) > 131072:
                    buf = b""                    # runaway guard on a garbled link
        except Exception:
            _ser_fd = None
            with _lock:
                _state["checked"] = time.time()
            time.sleep(2)                        # device gone; retry open


def serial_send_file(data, name, apply_after):
    """Stream a file to the board over serial, then optionally apply it."""
    crc = zlib.crc32(data) & 0xffffffff
    with _lock:
        _ota.update(active=True, name=name, size=len(data), sent=0, recv=0,
                    phase="send", ok=None, msg="", _put_ok=None)
    _ser_write({"t": "put", "name": name, "size": len(data), "crc": crc})
    CH = 4096
    for i in range(0, len(data), CH):
        _ser_write_raw(data[i:i + CH])
        with _lock:
            _ota["sent"] = min(i + CH, len(data))
    # wait for the board's put_result
    deadline = time.time() + 120
    while time.time() < deadline:
        with _lock:
            po = _ota.get("_put_ok")
        if po is not None:
            break
        time.sleep(0.1)
    with _lock:
        po = _ota.get("_put_ok")
    if not po:
        with _lock:
            _ota.update(phase="error", ok=False, active=False,
                        msg=_ota.get("msg") or "transfer failed/timed out")
        return
    if apply_after:
        with _lock:
            _ota.update(phase="apply")
        _ser_write({"t": "apply", "name": name})   # result arrives via _dispatch
    else:
        with _lock:
            _ota.update(phase="done", ok=True, active=False, msg="received")


def serial_provision(rfile, boot_size, rootfs_size):
    """Stream a blank-SD install to the installer: boot files then the rootfs,
    both read from the POST body and pushed to serial without buffering."""
    total = boot_size + rootfs_size
    with _lock:
        _ota.update(active=True, name="provision", size=total, sent=0, recv=0,
                    phase="send", ok=None, msg="", _put_ok=None)
    _ser_write({"t": "provision", "boot_size": boot_size, "rootfs_size": rootfs_size})
    sent = 0
    remaining = total
    while remaining > 0:
        chunk = rfile.read(min(65536, remaining))
        if not chunk:
            break
        _ser_write_raw(chunk)
        sent += len(chunk); remaining -= len(chunk)
        with _lock:
            _ota["sent"] = sent
    with _lock:
        _ota.update(phase="apply")          # board is now partitioning + writing
    # provisioning runs long; wait for apply_result via _dispatch
    deadline = time.time() + 1800
    while time.time() < deadline:
        with _lock:
            done = _ota["phase"] in ("done", "error")
        if done:
            break
        time.sleep(0.5)
    with _lock:
        if _ota["phase"] not in ("done", "error"):
            _ota.update(phase="error", ok=False, active=False, msg="provision timed out")


def stale_watchdog():
    """Flag offline when no telemetry line has arrived for STALE_AFTER seconds."""
    while True:
        time.sleep(POLL_INTERVAL)
        with _lock:
            if _state["last_ok"] and time.time() - _state["last_ok"] > STALE_AFTER:
                _state.update(conn="offline", checked=time.time())


class HubServer(ThreadingHTTPServer):
    # Skip HTTPServer.server_bind's socket.getfqdn() — a reverse-DNS lookup that
    # can hang for many seconds (notably on macOS) and only sets a cosmetic name.
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = "hub"
        self.server_port = self.server_address[1]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/healthz":
            return self._send(200, "ok", "text/plain")
        if self.path == "/api/status":
            with _lock:
                payload = dict(_state, age=round(time.time() - _state["checked"], 1))
            return self._send(200, json.dumps(payload), "application/json")
        if self.path == "/api/history":
            with _lock:
                pts = list(_history)
            return self._send(200, json.dumps({"points": pts}), "application/json")
        if self.path == "/api/ota/status":
            with _lock:
                o = {k: v for k, v in _ota.items() if not k.startswith("_")}
            return self._send(200, json.dumps(o), "application/json")
        return self._send(404, json.dumps({"err": "not found"}), "application/json")

    def do_POST(self):
        path = self.path.split("?")[0]
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        # POST /api/provision?boot_size=B&rootfs_size=S  body = boot.tgz + rootfs.tar.gz
        if path == "/api/provision":
            if not SERIAL_DEV or _ser_fd is None:
                return self._send(409, json.dumps({"err": "serial link not available"}), "application/json")
            with _lock:
                busy = _ota["active"]
            if busy:
                return self._send(409, json.dumps({"err": "busy"}), "application/json")
            try:
                bsz = int(q.get("boot_size", ["0"])[0]); rsz = int(q.get("rootfs_size", ["0"])[0])
            except ValueError:
                bsz = rsz = 0
            if bsz <= 0 or rsz <= 0:
                return self._send(400, json.dumps({"err": "boot_size & rootfs_size required"}), "application/json")
            threading.Thread(target=serial_provision, args=(self.rfile, bsz, rsz),
                             daemon=True).start()
            # respond after the transfer thread finishes reading the body
            deadline = time.time() + 2400
            while time.time() < deadline:
                with _lock:
                    ph = _ota["phase"]
                if ph in ("apply", "done", "error"):
                    break
                time.sleep(0.3)
            with _lock:
                o = {k: v for k, v in _ota.items() if not k.startswith("_")}
            return self._send(200, json.dumps({"accepted": True, "ota": o}), "application/json")
        # POST /api/ota?name=<f>&apply=1  with the raw file as the request body.
        if path != "/api/ota":
            return self._send(404, json.dumps({"err": "not found"}), "application/json")
        if not SERIAL_DEV or _ser_fd is None:
            return self._send(409, json.dumps({"err": "serial link not available"}), "application/json")
        with _lock:
            busy = _ota["active"]
        if busy:
            return self._send(409, json.dumps({"err": "transfer already in progress"}), "application/json")
        name = os.path.basename(q.get("name", ["upload.swu"])[0]) or "upload.swu"
        apply_after = q.get("apply", ["0"])[0] in ("1", "true", "yes")
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        if n <= 0:
            return self._send(400, json.dumps({"err": "empty body"}), "application/json")
        data = self.rfile.read(n)
        threading.Thread(target=serial_send_file, args=(data, name, apply_after),
                         daemon=True).start()
        return self._send(202, json.dumps({"accepted": True, "name": name,
                          "size": len(data), "apply": apply_after}), "application/json")


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SAM9X60 Connect — Hub</title>
<style>
:root{--bg:#0f1115;--card:#1a1d24;--fg:#e6e9ef;--mut:#8b93a7;--line:#272b34;
      --ok:#2ecc71;--warn:#f1c40f;--err:#e74c3c;--acc:#4aa3ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:20px}
h1{font-size:19px;margin:0 0 2px}.sub{color:var(--mut);font-size:13px;margin-bottom:18px}
.banner{border-radius:12px;padding:16px 18px;margin-bottom:18px;display:flex;
  align-items:center;gap:14px;font-weight:600;border:1px solid var(--line)}
.dot{width:14px;height:14px;border-radius:50%;flex:0 0 auto}
.b-ready{background:#12261a}.b-ready .dot{background:var(--ok);box-shadow:0 0 12px var(--ok)}
.b-installer{background:#2a2410}.b-installer .dot{background:var(--warn);box-shadow:0 0 12px var(--warn)}
.b-offline{background:#2a1414}.b-offline .dot{background:var(--err);box-shadow:0 0 12px var(--err)}
.b-unknown{background:#1a1d24}.b-unknown .dot{background:var(--mut)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.k{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.v{font-size:20px;font-weight:600;margin-top:3px}.v small{font-size:13px;color:var(--mut);font-weight:400}
.leds{display:flex;gap:8px;margin-top:6px}.led{width:16px;height:16px;border-radius:50%;
  border:1px solid var(--line);opacity:.25}.led.on{opacity:1}
.led.red{background:#e74c3c}.led.green{background:#2ecc71}.led.blue{background:#4aa3ff}
.chart{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-top:12px}
.chart .k{margin-bottom:8px}.chart svg{display:block;width:100%;height:120px;overflow:visible}
.chart .cur{fill:var(--fg);font-size:13px;font-weight:600}.chart .ax{fill:var(--mut);font-size:11px}
.ota{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-top:12px}
.ota h3{margin:0 0 10px;font-size:15px}.ota .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.ota input[type=file]{color:var(--mut);font-size:13px;max-width:100%}
.ota label{color:var(--mut);font-size:13px}
.ota button{background:var(--acc);color:#04203f;border:0;border-radius:7px;padding:8px 14px;font-weight:600;cursor:pointer}
.ota button:disabled{opacity:.5;cursor:default}
.bar{height:8px;background:#0d0f14;border-radius:5px;overflow:hidden;margin-top:10px}
.bar>div{height:100%;width:0;background:var(--acc);transition:width .2s}
.ota .st{color:var(--mut);font-size:12px;margin-top:6px}
.guide{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-top:14px}
.guide h3{margin:0 0 8px;font-size:15px}.guide code{background:#0d0f14;padding:2px 6px;border-radius:5px;
  color:var(--acc);font-size:13px}.guide ol{margin:8px 0 0 18px;padding:0}.guide li{margin:4px 0}
.foot{color:var(--mut);font-size:12px;margin-top:18px}
</style></head><body><div class="wrap">
<h1>SAM9X60 Connect · Hub</h1>
<div class="sub" id="sub">connecting…</div>
<div class="banner b-unknown" id="banner"><span class="dot"></span><span id="btext">…</span></div>
<div id="body"></div>
<div id="ota"></div>
<div class="foot" id="foot"></div>
</div>
<script>
const $=id=>document.getElementById(id);
function fmtUp(s){if(s==null)return'–';const d=s/86400|0,h=s%86400/3600|0,m=s%3600/60|0;
  return d?`${d}d ${h}h`:h?`${h}h ${m}m`:`${m}m`}
function card(k,v){return `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`}
async function drawChart(){
 const el=$('chart'); if(!el) return;
 let pts;try{pts=(await (await fetch('/api/history')).json()).points}catch(e){return}
 const el2=$('chart'); if(!el2) return;            // dashboard may have re-rendered
 const xs=pts.filter(p=>p.temp!=null);
 if(xs.length<2){el2.innerHTML='<div class="k">Temperature</div><div class="ax">collecting…</div>';return}
 const W=680,H=120,P=6, vals=xs.map(p=>p.temp);
 let lo=Math.min(...vals),hi=Math.max(...vals); if(hi-lo<1){hi+=0.5;lo-=0.5}
 const x=i=>P+i*(W-2*P)/(xs.length-1), y=v=>P+(1-(v-lo)/(hi-lo))*(H-2*P);
 const line=vals.map((v,i)=>`${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
 const area=`${P},${H-P} ${line} ${x(xs.length-1)},${H-P}`;
 const cur=vals[vals.length-1], mins=Math.round((xs[xs.length-1].ts-xs[0].ts)/60);
 el2.innerHTML=`<div class="k">Temperature · last ${mins||'<1'} min · via USB</div>
  <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
   <polygon points="${area}" fill="rgba(74,163,255,.12)"/>
   <polyline points="${line}" fill="none" stroke="var(--acc)" stroke-width="2"/>
   <circle cx="${x(xs.length-1).toFixed(1)}" cy="${y(cur).toFixed(1)}" r="3" fill="var(--acc)"/>
   <text class="cur" x="${(x(xs.length-1)-4).toFixed(1)}" y="${(y(cur)-8).toFixed(1)}" text-anchor="end">${cur}°C</text>
   <text class="ax" x="2" y="12">${hi.toFixed(1)}</text>
   <text class="ax" x="2" y="${H-2}">${lo.toFixed(1)}</text>
  </svg>`;
}
async function tick(){
 let st;try{st=await (await fetch('/api/status')).json()}catch(e){return}
 const b=$('banner'),body=$('body');
 b.className='banner b-'+st.conn;
 const src=st.board.source||st.board.host||'?';
 const transport=st.board.transport||'http';
 renderOta(transport);
 const host=st.board.host||src;
 if(st.conn==='ready'){
   const s=st.sysinfo||{},hr=st.hr||{};
   $('btext').textContent=`Board READY — ${s.hostname||src}`;
   const leds=s.leds||{};
   const ledhtml=['red','green','blue'].map(c=>`<span class="led ${c} ${leds[c]&&leds[c]!=='off'?'on':''}"></span>`).join('');
   body.innerHTML=`<div class="grid">
     ${card('Slot',(s.slot||'?')+' <small>'+(s.slot_part||'')+'</small>')}
     ${card('Temp',(s.temp!=null?s.temp+' <small>°C</small>':'–'))}
     ${card('Uptime',fmtUp(s.uptime_s))}
     ${card('Load',s.load1!=null?s.load1:'–')}
     ${card('IP',s.ip||host)}
     ${card('Kernel','<small>'+(s.kernel||'–')+'</small>')}
     ${'<div class="card"><div class="k">LEDs</div><div class="leds">'+ledhtml+'</div></div>'}
     ${card('Heart rate',hr&&hr.bpm?hr.bpm+' <small>bpm</small>':'<small>'+((hr&&hr.state)||'idle')+'</small>')}
   </div>
   <div class="chart" id="chart"></div>`;
   drawChart();
 }else if(st.conn==='installer'){
   const inst=st.installer||{};
   const sd=inst.sd; const sdtxt=sd==='blank'?'blank / unprovisioned':sd==='ok'?'present':sd||'unknown';
   $('btext').textContent='RECOVERY INSTALLER — provision the SD over USB';
   body.innerHTML=`<div class="guide"><h3>Blank-SD wizard</h3>
     <div>The board fell back to the QSPI recovery installer (SD: <code>${sdtxt}</code>).
     Reinstall the system over the USB link — no network, no card reader:</div>
     <ol><li>Below, pick the <b>provisioning <code>.swu</code></b> and tick <b>apply after transfer</b></li>
     <li>Push it — the installer partitions the SD and writes bootloader + rootfs</li>
     <li>On success it reboots into the normal A/B system</li></ol></div>`;
 }else if(st.conn==='offline'){
   $('btext').textContent='Board OFFLINE';
   body.innerHTML=`<div class="guide"><h3>No response from the board</h3>
     <div>Nothing is answering at <code>${host}</code>. Check, in order:</div>
     <ol><li>Power / USB cable</li><li>Insert a provisioned SD card (or let the QSPI installer come up)</li>
     <li>Wi-Fi join — confirm the board got a LAN IP</li></ol></div>`;
 }else{$('btext').textContent='connecting…';}
 $('sub').textContent=`${transport==='serial'?'USB serial':'HTTP'} · ${src}`;
 $('foot').textContent=`updated ${st.age}s ago · state "${st.conn}" · via ${transport}`;
}
function fmtB(n){n=n||0;return n>=1048576?(n/1048576).toFixed(1)+'MB':n>=1024?(n/1024|0)+'KB':n+'B';}
let otaRendered=false;
function renderOta(transport){
 if(otaRendered||transport!=='serial')return; otaRendered=true;
 $('ota').innerHTML=`<div class="ota"><h3>Push update (.swu) over USB</h3>
   <div class="row">
     <input type="file" id="swu" accept=".swu,.bin,.img">
     <label><input type="checkbox" id="apply"> apply after transfer</label>
     <button id="push">Push over USB</button>
   </div>
   <div class="bar"><div id="pbar"></div></div>
   <div class="st" id="pst">idle</div></div>`;
 $('push').onclick=pushSwu;
}
async function pushSwu(){
 const f=$('swu').files[0]; if(!f){$('pst').textContent='pick a .swu first';return;}
 $('push').disabled=true; $('pst').textContent='reading…';
 const buf=await f.arrayBuffer();
 try{await fetch(`/api/ota?name=${encodeURIComponent(f.name)}&apply=${$('apply').checked?1:0}`,{method:'POST',body:buf});}
 catch(e){$('pst').textContent='upload failed';$('push').disabled=false;return;}
 pollOta();
}
async function pollOta(){
 let o;try{o=await (await fetch('/api/ota/status')).json()}catch(e){setTimeout(pollOta,600);return;}
 const cur=o.phase==='send'?o.sent:o.recv, pct=o.size?Math.round(cur/o.size*100):0;
 $('pbar').style.width=pct+'%';
 const lbl={send:'sending',apply:'applying',done:'done ✓',error:'error',idle:'idle'}[o.phase]||o.phase;
 $('pst').textContent=`${lbl} · ${fmtB(cur)}/${fmtB(o.size)}${o.msg?' · '+o.msg:''}`;
 if(o.active) setTimeout(pollOta,500); else $('push').disabled=false;
}
const INSTALLER_PORT=%%INSTALLER_PORT%%;
tick();setInterval(tick,3000);
</script></body></html>"""
PAGE = PAGE.replace("%%INSTALLER_PORT%%", str(INSTALLER_PORT))


if __name__ == "__main__":
    if SERIAL_DEV:
        threading.Thread(target=serial_loop, daemon=True).start()
        threading.Thread(target=stale_watchdog, daemon=True).start()
        src = f"serial {SERIAL_DEV}"
    else:
        threading.Thread(target=poll_loop, daemon=True).start()
        src = f"http {BOARD_HOST}:{BOARD_PORT}"
    print(f"hub on :{HUB_PORT}, source {src}", flush=True)
    HubServer(("0.0.0.0", HUB_PORT), Handler).serve_forever()
