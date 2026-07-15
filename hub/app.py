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
import os, json, time, threading, socketserver, termios, urllib.request, urllib.error
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


def serial_loop():
    """Read newline-delimited JSON telemetry from the board's CDC-ACM gadget."""
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
            buf = b""
            while True:
                chunk = os.read(fd, 512)
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    try:
                        obj = json.loads(raw.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    now = time.time()
                    with _lock:
                        _state.update(conn="ready", sysinfo=obj.get("sysinfo"),
                                      hr=obj.get("hr"), last_ok=now, checked=now)
                    _record(obj.get("sysinfo"), obj.get("hr"))
                if len(buf) > 65536:
                    buf = b""                    # runaway guard on a garbled link
        except Exception:
            with _lock:
                _state["checked"] = time.time()
            time.sleep(2)                        # device gone; retry open


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
        return self._send(404, json.dumps({"err": "not found"}), "application/json")


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
.guide{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-top:14px}
.guide h3{margin:0 0 8px;font-size:15px}.guide code{background:#0d0f14;padding:2px 6px;border-radius:5px;
  color:var(--acc);font-size:13px}.guide ol{margin:8px 0 0 18px;padding:0}.guide li{margin:4px 0}
.foot{color:var(--mut);font-size:12px;margin-top:18px}
</style></head><body><div class="wrap">
<h1>SAM9X60 Connect · Hub</h1>
<div class="sub" id="sub">connecting…</div>
<div class="banner b-unknown" id="banner"><span class="dot"></span><span id="btext">…</span></div>
<div id="body"></div>
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
   $('btext').textContent='RECOVERY INSTALLER running — system not installed';
   body.innerHTML=`<div class="guide"><h3>The board booted its QSPI recovery installer</h3>
     <div>The SD card is blank or broken, so the board fell back to flash. Reinstall the system:</div>
     <ol><li>Reach the installer's SWUpdate at <code>http://${host}:${INSTALLER_PORT}/</code></li>
     <li>Upload a <code>.swu</code> system image</li>
     <li>The board writes the SD card and reboots into the normal A/B system</li></ol></div>`;
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
