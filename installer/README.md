# installer/ — QSPI recovery installer *(roadmap M2)*

A tiny, RAM-only system stored in the board's 16 MB QSPI flash that installs (or
recovers) the full system onto the SD card over USB. Placeholder — implementation
lands in milestone **M2**.

How it will work:

1. The SoC boot ROM tries the SD card first, then falls through to QSPI. A blank
   or corrupted SD therefore boots this installer automatically.
2. The installer runs from an initramfs (no SD needed), brings up the USB gadget,
   and waits for the host.
3. The host pushes a full-system `.swu`; the installer partitions the SD, formats
   it, writes bootloader + kernel FIT + rootfs, and reboots into the installed
   system.

It uses **SWUpdate** — the same engine as OTA (L1) — so there is one image format
for both provisioning and updates. The recovery image (kernel + rescue initramfs:
busybox, SWUpdate, `mkfs.ext4`/`mkfs.vfat`, `fw_setenv`, USB gadget) fits within
the 16 MB QSPI budget alongside the bootstrap and U-Boot.
