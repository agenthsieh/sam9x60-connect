#!/bin/sh
# provision.sh — install a fresh system onto a blank SD from the recovery
# installer. The board has only 128 MB RAM, far less than a rootfs, so the
# rootfs is *streamed* straight onto the SD (never buffered): the caller pipes
# a gzipped rootfs tar on stdin and we extract it on the fly.
#
# Usage (from installer-agent.sh):
#     provision.sh <boot_dir>  < rootfs.tar.gz
#   <boot_dir>  a small dir already unpacked in tmpfs holding the FAT boot files
#               (boot.bin, u-boot.bin, <kernel>.itb, uboot.env)
#   stdin       the gzipped rootfs tarball, streamed
#
# Partition map reproduces the stock SD exactly:
#   p1 2048 +32768 (16M) FAT bootable | p2/p3 900M ext4 A/B | p4 rest ext4
set -e
BOOTDIR="$1"
DISK=/dev/mmcblk0
log() { echo "[provision] $*"; }

[ -d "$BOOTDIR" ] || { echo "no boot dir: $BOOTDIR"; exit 1; }

log "partitioning $DISK"
sfdisk "$DISK" <<'PART'
label: dos
unit: sectors
start=2048,    size=32768,   type=c, bootable
start=34816,   size=1843200, type=83
start=1878016, size=1843200, type=83
start=3721216, type=83
PART
sync; sleep 1
sfdisk -R "$DISK" 2>/dev/null || true
sleep 1

log "formatting"
mkfs.vfat -n BOOT "${DISK}p1" >/dev/null
for p in p2 p3 p4; do mke2fs -t ext4 -F -q "${DISK}${p}"; done

log "writing boot (p1)"
mkdir -p /mnt/p1; mount "${DISK}p1" /mnt/p1
cp "$BOOTDIR"/* /mnt/p1/
sync; umount /mnt/p1

log "streaming rootfs to slot A (p2)"
mkdir -p /mnt/r
mount "${DISK}p2" /mnt/r
tar xz -C /mnt/r            # <-- rootfs.tar.gz streamed from stdin, not buffered
sync

log "cloning slot A -> slot B (p3)"
mount "${DISK}p3" /mnt/rb 2>/dev/null || { mkdir -p /mnt/rb; mount "${DISK}p3" /mnt/rb; }
cp -a /mnt/r/. /mnt/rb/
sync; umount /mnt/rb; umount /mnt/r

log "done"
sync
