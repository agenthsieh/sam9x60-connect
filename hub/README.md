# hub — NAS management console *(milestone M3, in progress)*

A tiny web console that runs as a Docker container (built for a Synology NAS
via Container Manager, but it's plain Docker) and watches a SAM9X60 board over
the LAN. It polls the board's on-device API and shows:

- **Board READY** — live status: A/B slot, kernel, uptime, load, temperature,
  heart rate, LED state.
- **RECOVERY INSTALLER running** — the SD card is blank/broken and the board
  fell back to the [QSPI installer](../board/ota/recovery/README.md); the page
  guides you to push a `.swu` to reinstall.
- **Board OFFLINE** — nothing answering; power / SD / Wi-Fi checklist.

No third-party dependencies (Python stdlib `http.server` only), so the image is
tiny and there's nothing to pip-install.

## How it reaches the board

**Over USB, not Wi-Fi** — that's the point of a *connected* device.

The board streams JSON telemetry over a **USB-serial (CDC-ACM) gadget** (see
[`../board/usb-serial/`](../board/usb-serial/)). The host exposes it as
`/dev/ttyACM0`; the hub reads one status line (~every 2 s) and never touches the
network to talk to the board.

```
board /dev/ttyGS0 ──USB CDC-ACM──► host /dev/ttyACM0 ──device──► hub :8091
   (usb_serial_status.py: {"sysinfo":{…},"hr":{…}} per line, every 2 s)
```

Why serial and not a USB-Ethernet gadget? A CDC-ECM/RNDIS gadget would let the
hub poll the board's HTTP API over USB, but it needs `usbnet` (+`mii`) on the
host — and a Synology NAS ships `cdc-acm` but **not** the usbnet/mii stack. A
serial gadget only needs `cdc-acm`, which every host has.

The line payload reuses the board's own `/api/sysinfo` (slot, kernel, uptime,
load, ip, temp, LEDs) and `/api/hr/data`, so there's one source of truth. No
SSH or credentials anywhere.

**HTTP fallback.** On a host that *can* do usbnet, or for a quick LAN test, set
`BOARD_HOST=<ip>` instead of `SERIAL_DEV`; the hub then polls the board's web
API (and can detect the recovery installer on `:8090`).

### Host setup (Synology)

`cdc-acm` isn't auto-loaded and doesn't survive a reboot, so load it on boot
(DSM → Task Scheduler → triggered "Boot-up" task, user `root`):

```sh
/sbin/insmod /lib/modules/cdc-acm.ko 2>/dev/null || /sbin/modprobe cdc-acm
```

Once the board is plugged in and `cdc-acm` is loaded, `/dev/ttyACM0` appears and
the container picks it up.

## Run

```sh
cp .env.example .env          # SERIAL_DEV=/dev/ttyACM0 (default) for USB serial
docker compose up -d --build
# open http://<nas-ip>:8091/
```

On Synology, run it from Container Manager (Project → point at this folder) or
over SSH with `sudo docker compose ...`.

## Config (`.env`)

| var | default | meaning |
|-----|---------|---------|
| `SERIAL_DEV` | `/dev/ttyACM0` | CDC-ACM device — enables USB-serial mode |
| `BOARD_HOST` | — | board IP for HTTP-poll mode (leave unset for serial) |
| `BOARD_PORT` | 8080 | on-device web console (HTTP mode) |
| `INSTALLER_PORT` | 8090 | recovery SWUpdate port (probed in HTTP mode) |
| `POLL_INTERVAL` | 3 | seconds between polls/checks |
| `STALE_AFTER` | 8 | seconds without telemetry → OFFLINE |
| `HUB_PORT` | 8091 | port the hub listens on |

Serial mode maps the device into the container and runs it as root (the
CDC-ACM node is root-owned on the host) — see `docker-compose.yml`.

## Endpoints

- `GET /` — dashboard (auto-refreshes every 3 s)
- `GET /api/status` — aggregated JSON (`conn`, `sysinfo`, `hr`, `board`, `age`)
- `GET /healthz` — container healthcheck

## Roadmap (M3/M4)

- Push a `.swu` to the board straight from the dashboard (OTA button).
- Blank-SD provisioning wizard driven from the installer.
- MQTT telemetry ingest (temp / heart rate) for history + charts (M4).
