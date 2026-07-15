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

**Over USB, not Wi-Fi** — that's the point of a *connected* device. Plug the
board's USB into the host; it enumerates a CDC-ECM link and the board comes up
at `192.168.9.1` (host side `192.168.9.2`). Board data never touches Wi-Fi. Run
[`nas-usb-up.sh`](nas-usb-up.sh) once on the host to bring up the host side of
the link (Synology doesn't auto-configure it). Set `BOARD_HOST=192.168.9.1`.

```
board USB ──CDC-ECM──► host 192.168.9.2 ── Docker ──► hub :8091
                                                        │ HTTP poll
                          board 192.168.9.1:8080 ◄──────┘  (/api/sysinfo, /api/hr/data)
                          board 192.168.9.1:8090 ◄──────── probe (recovery SWUpdate)
```

The hub consumes the board's `/api/sysinfo` (added for the hub — slot, kernel,
uptime, load, IP, temp, LEDs) plus `/api/hr/data`. No SSH or credentials in the
container. (A Wi-Fi LAN IP also works as a fallback — just set `BOARD_HOST` to
it — but the USB link is the intended path.)

## Run

```sh
cp .env.example .env          # set BOARD_HOST to the board's LAN IP
docker compose up -d --build
# open http://<nas-ip>:8091/
```

On Synology, run it from Container Manager (Project → point at this folder) or
over SSH with `sudo docker compose ...`.

## Config (`.env`)

| var | default | meaning |
|-----|---------|---------|
| `BOARD_HOST` | — (required) | board LAN IP / hostname |
| `BOARD_PORT` | 8080 | on-device web console |
| `INSTALLER_PORT` | 8090 | recovery SWUpdate port (probed to detect installer mode) |
| `POLL_INTERVAL` | 3 | seconds between polls |
| `HUB_PORT` | 8091 | port the hub listens on |

## Endpoints

- `GET /` — dashboard (auto-refreshes every 3 s)
- `GET /api/status` — aggregated JSON (`conn`, `sysinfo`, `hr`, `board`, `age`)
- `GET /healthz` — container healthcheck

## Roadmap (M3/M4)

- Push a `.swu` to the board straight from the dashboard (OTA button).
- Blank-SD provisioning wizard driven from the installer.
- MQTT telemetry ingest (temp / heart rate) for history + charts (M4).
