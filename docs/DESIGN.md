# Design

`sam9x60-connect` turns a SAM9X60 board into a USB-connected device platform.
Four layers, from the boot ROM up:

```
 L3  Data      MQTT telemetry · file sync · management API
 L2  Link      USB gadget (CDC-ECM) · on-board DHCP · host hub
 L1  OTA       SWUpdate + A/B slots · web upload · health-check rollback
 L0  Boot      normal boot from SD (A/B)  |  QSPI recovery installer
```

## L0 — Boot & provisioning

**Normal boot (SD, A/B).** U-Boot picks a slot from its environment
(`bootslot`, `bootcount`, `upgrade_available`, `bootlimit`), loads the matching
kernel FIT + rootfs, and boots. On a successful boot the OS clears the boot
counter; if a freshly-updated slot fails to boot N times, U-Boot rolls back to
the other slot.

**QSPI recovery installer.** The SoC boot ROM tries the SD card first and falls
through to QSPI flash. So a **blank or corrupted SD** automatically boots a tiny
installer stored in the 16 MB QSPI:

1. The installer boots entirely from RAM (initramfs) — it needs no SD.
2. It brings up the USB gadget; the host connects.
3. The host pushes a full-system image; the installer partitions the SD, writes
   the bootloader + kernel + rootfs, and reboots into the installed system.

The same mechanism serves **factory provisioning** and **field recovery**, all
over the one USB cable. The installer uses SWUpdate — the same engine as OTA —
so there's one update format to learn. A full recovery image (kernel + rescue
initramfs with busybox, SWUpdate, mkfs, fw_setenv, USB gadget) fits comfortably
in the 16 MB budget.

## L1 — OTA (SWUpdate + A/B)

Updates cover **apps + kernel/dtb + rootfs** — but **not** the bootloader (that
would risk bricking; the bootloader is only touched by the QSPI installer).

- **A/B rootfs slots** on the SD: write the *inactive* slot, verify, flip the
  U-Boot slot pointer, reboot, health-check, auto-rollback on failure. Atomic and
  power-safe.
- **Kernel FIT** lives on the small FAT boot partition; two copies (A/B) are kept
  there.
- A dedicated **/data** partition survives updates (config, state, user data).
- The board runs SWUpdate with its built-in web server, so an update is a
  drag-and-drop `.swu` upload with a live progress bar — reachable over the USB
  link.

## L2 — Link (USB gadget)

The board enumerates as a **CDC-ECM** USB Ethernet gadget. The host sees a new
network interface; a minimal on-board DHCP server (bound only to the USB
interface) hands it an address on `192.168.9.0/24`.

Two deliberate choices:

- **CDC-ECM**, not NCM/RNDIS: it's the common denominator across macOS, Linux,
  and Synology DSM (all ship the `cdc_ether` host driver). NCM is faster but
  Synology doesn't ship it; RNDIS works on Windows/Linux but not macOS.
- **The on-board DHCP hands out no gateway and no DNS.** A host-to-host link must
  not become the host's default route — otherwise the host tries to reach the
  internet (and resolve DNS) through the board and loses connectivity. Handing
  out only an address + subnet keeps the USB link a private side-channel.

**Host hub.** For a permanently-attached host like a NAS, a single container
manages the device end-to-end: connection, OTA push, telemetry ingest, file
sync, and a management UI. On a Synology NAS this is a Docker container (via
Container Manager); on a laptop you can just use the board's own web UI + a CLI.

## L3 — Data sharing

The link is intermittent (the cable gets unplugged; either side reboots), so the
model is **"each side keeps a local data area and they sync"** — not "the host
polls the board". Polling loses data across disconnects; a local buffer plus
sync-on-reconnect (store-and-forward) does not.

Sync is **asymmetric** — each kind of data has a single source of truth, so there
is no conflict resolution to get wrong:

| Data | Direction | Transport | While disconnected |
|------|-----------|-----------|--------------------|
| Sensor telemetry | device → host | MQTT (broker on the host) | queued in `/data`, replayed on reconnect |
| Files / blobs | two-way, per-dir owner | rsync on connect | kept locally, reconciled on reconnect |
| Config / commands | host → device | host → device API | device uses last-known config |
| Live glance | host pulls | read-only REST | (supplement only, not the sync path) |

The device's local data area is the same **/data** partition the OTA design keeps
across updates.

## Constraints worth knowing

- The FAT boot partition is small (~16 MB) — two kernel FITs fit, but not much
  else. The kernel image (an already-compressed ARM `zImage`) can't be shrunk by
  re-compressing; to make it smaller you must drop built-in drivers.
- On the SAM9X60, U-Boot loads the kernel FIT from FAT (no ext4/raw loader), so
  A/B slot selection rides on the U-Boot environment, not on a rootfs path.
- The bootloader is never updated over the air — only via the QSPI installer or a
  vendor tool (SAM-BA). This is a safety choice.
