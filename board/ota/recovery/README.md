# QSPI recovery installer *(milestone M2)*

A self-contained installer that lives in the 16 MB QSPI flash and boots when the
SD card is blank or broken. It brings up the USB-gadget link and a
[SWUpdate](https://swupdate.org) endpoint so a host can (re)install the whole
system — the "last-resort" half of the update story that complements the
[A/B OTA](../README.md) running on a healthy system.

## Boot chain

The SAM9X60 boot ROM tries SDMMC0 first, then QSPI. So the trigger is simply
whether the SD card has a valid `boot.bin`:

```
boot.bin present  → SD:  at91bootstrap → u-boot → A/B rootfs   (normal)
boot.bin absent   → QSPI: at91bootstrap → u-boot → FIT installer (recovery)
```

QSPI layout:

```
0x000000  at91bootstrap
0x040000  u-boot            (BOOTCOMMAND: sf read 0x24000000 0x200000 0xe00000; bootm 0x24000000)
0x140000  u-boot env
0x200000  FIT  (kernel + dtb + initramfs)
```

The u-boot in QSPI must read enough of the flash to cover the whole FIT — the
stock 0x740000 (7.25 MB) read truncates a real installer and yields
`Bad FIT kernel image format`. This build reads 0xe00000 (14 MB).

## Two things that will waste your day

**1. Use the full kernel.** A slimmed-down kernel hangs silently right after
`Loading compiled-in X.509 certificates` — no panic, no console, nothing. The
exact `zImage` that boots the A/B slots boots the installer fine. The size cost
(≈5.9 MB) fits the read window.

**2. The initramfs needs the whole C runtime.** busybox and swupdate are
dynamically linked against `/lib/ld-linux.so.3` + `libc.so.6` (+ `libm`,
`libtirpc`, `libresolv`, …). Copy only the app libs and you get:

```
Run /init as init process
Failed to execute /init (error -2)          ← ENOENT: no interpreter
Kernel panic - not syncing: No working init found.
```

because *nothing* — not `/init`, not `/bin/sh`, not `/bin/busybox` — can be
execve'd. [`build-initramfs.sh`](build-initramfs.sh) resolves the full
`readelf -d` closure instead of a hand-picked list. Two related details:
`/init`'s shebang is `#!/bin/busybox sh` (there is no `/bin/sh` until
`busybox --install` runs), and the recovery kernel has `CONFIG_DEVTMPFS_MOUNT=y`
so no `/dev` nodes are needed in the cpio (build it unprivileged).

## Build & flash

```sh
BR=/path/to/buildroot/output ./build-initramfs.sh          # -> inst-initramfs.cpio.gz
./build-fit.sh $BR/images/zImage board.dtb inst-initramfs.cpio.gz recovery.itb
# copy recovery.itb to the running board, then:
./flash-qspi.sh recovery.itb                                # writes QSPI @ 0x200000, SD untouched
```

Flashing is safe from a normal SD boot — it only writes QSPI, and the installer
stays dormant until `boot.bin` goes missing.

## Verified end-to-end

```
rename SD boot.bin -> boot.bin.off ; reboot
  → boot ROM falls through to QSPI
  → u-boot reads the FIT, boots the full kernel + installer initramfs
  → "=== SAM9X60 QSPI RECOVERY INSTALLER ===", usb0 = 192.168.9.1, swupdate on :8090
  → serial rescue shell
recover without an SD-card reader, from the installer's own shell:
  mount /dev/mmcblk0p1 /mnt && mv /mnt/boot.bin.off /mnt/boot.bin && sync && reboot -f
  → boots back to the A/B system
```

## USB gadget must connect on boot, not just on a VBUS edge

The atmel UDC connects to the host only on a **VBUS rising edge**. Since the
board stays plugged into the host across a reboot, VBUS never drops — so the
gadget loads but never enumerates, and the host sees nothing until a physical
re-plug. `vbus_is_present()` returns 1 ("assume present") when there is *no*
vbus GPIO, so the fix is to **drop `atmel,vbus-gpio`** from the gadget node in
the recovery FIT's dtb — the UDC then connects whenever a gadget binds (i.e. on
boot):

```sh
fdtput -d recovery.dtb /ahb/gadget@500000 atmel,vbus-gpio   # -> always-on UDC
```

Apply the same to the SD kernel FIT's base dtb so normal-system telemetry also
survives a reboot. (Verified: the recovery installer enumerates on the host at
boot with no re-plug once vbus-gpio is removed.)

## Known gaps (tracked for later)

- **Host reachability of the gadget.** The installer has no DHCP server
  (busybox ships `udhcpc`, not `udhcpd`), so the host must set `192.168.9.2`
  statically to reach `:8090`. A cleaner path is to apply a `.swu` found on a
  locally-mounted disk, needing no host network at all.
- Provisioning a blank SD (partition + write bootloader + rootfs) from the
  installer is the next milestone.
