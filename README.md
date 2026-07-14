# sam9x60-connect

Turn a Microchip **SAM9X60** board into a USB-connected device platform: plug it
into any host — a Mac, a Linux box, or a Synology NAS — over a **single USB
cable** and get an IP network link, over-the-air (OTA) updates, and two-way data
sync. No serial console, no extra software on the host.

> **Status: early development.** The USB-gadget connectivity layer works today.
> OTA (A/B [SWUpdate](https://swupdate.org)), the QSPI recovery installer, and the
> host-side hub are on the [roadmap](#roadmap).

## Why

The SAM9X60 is a cheap, fun ARM926 Linux SoC (e.g. the *APP-SAM9X60 Hobby Kit*).
This project makes it a **connected** device:

- **One USB cable = a network link.** The board enumerates as a USB **CDC-ECM**
  Ethernet gadget. The host sees a new network interface and gets an IP
  automatically — native on macOS, Linux, and Synology DSM (no drivers, no host
  install). Measured ~0.7 ms latency, far snappier than Wi-Fi.
- **OTA updates.** Update the whole system (apps + kernel/dtb + rootfs) with
  **A/B redundancy and automatic rollback**, driven by SWUpdate.
- **Recover a blank or bricked SD card** over the *same* USB cable, using a tiny
  installer that lives in the board's 16 MB QSPI flash — no card removal, no
  card reader.
- **Two-way data sync.** Stream sensor telemetry to the host (MQTT) and sync
  files both ways, tolerant of the cable being unplugged (store-and-forward).

## Architecture

```
 L3  Data      MQTT telemetry · file sync · management API
 L2  Link      USB gadget (CDC-ECM) · on-board DHCP · host hub (NAS: Docker)
 L1  OTA       SWUpdate + A/B slots · web upload · health-check rollback
 L0  Boot      normal boot from SD (A/B)  |  QSPI recovery installer (blank/dead SD)
```

The board owns a small on-board network (`192.168.9.0/24`, board = `.1`) and runs
a minimal DHCP server on the USB interface, so the host auto-configures with zero
setup. See [`docs/DESIGN.md`](docs/DESIGN.md) for the full design.

## Repository layout

| Path | What |
|------|------|
| [`board/`](board/) | Code that runs on the SAM9X60 (USB gadget bring-up, DHCP, OTA agent) |
| [`installer/`](installer/) | QSPI recovery/installer image *(roadmap M2)* |
| [`hub/`](hub/) | Host-side hub, e.g. a Docker container for a NAS *(roadmap M3)* |
| [`docs/`](docs/) | Design docs |

## Quick start — connectivity (works today)

On the board (Linux, run once; a systemd unit makes it persist):

```sh
# 1. bring up the USB gadget ethernet + give it an IP
sudo cp board/usbnet-up.sh /usr/local/sbin/ && sudo chmod +x /usr/local/sbin/usbnet-up.sh
sudo cp board/usb_dhcp.py /root/
sudo cp board/usbnet.service /etc/systemd/system/
sudo systemctl enable --now usbnet
```

Then plug the board's **USB device port** (the micro-USB / USB-A wired to the
SoC's device controller, *not* a host port) into your computer. The host gets
`192.168.9.2` automatically, and you can reach the board at `192.168.9.1`:

```sh
ssh root@192.168.9.1        # or curl http://192.168.9.1:8080/ , etc.
```

### Host support

| Host | USB Ethernet class | Works out of the box? |
|------|--------------------|:--:|
| macOS | CDC-ECM | ✅ |
| Linux | CDC-ECM / NCM / RNDIS | ✅ |
| Synology DSM | CDC-ECM (`cdc_ether`) | ✅ |
| Windows | RNDIS (also offered by `g_ether`) | ✅ |

> The board advertises CDC-ECM because it's the common denominator across
> macOS + Synology + Linux. The on-board DHCP intentionally does **not** hand out
> a gateway or DNS, so it never hijacks the host's internet.

## Roadmap

| Milestone | Goal |
|-----------|------|
| **M1** | OTA on the SD card — A/B partitions + U-Boot slot selection + SWUpdate + web upload + rollback |
| **M2** | QSPI recovery installer — boot from flash on a blank/dead SD and install the full system over USB |
| **M3** | Host hub — a Docker container (for a NAS) that manages updates, telemetry, and file sync |
| **M4** | Data sync — MQTT telemetry (device → host) + rsync file sync, store-and-forward |

## Hardware

Developed on the **Microchip APP-SAM9X60 Hobby Kit** (SAM9X60-D1G SiP: ARM926 @
600 MHz + 128 MB DDR2, micro-SD boot, 16 MB QSPI NOR, USB host + device).

## License

[MIT](LICENSE) — use it, fork it, have fun.
