#!/bin/sh
# provision.sh <payload.tar.gz> — install a fresh system onto a blank SD from
# the recovery installer. The payload is a gzip tarball:
#     boot/              files for the FAT boot partition (p1)
#                        boot.bin, u-boot.bin, <kernel>.itb, uboot.env
#     rootfs.tar.gz      the root filesystem (written to slot A=p2 and B=p3)
#
# Partition map reproduces the stock SD exactly (fixed sectors, so p1 stays a
# 16 MB FAT boot area and the A/B slots are 900 MB each):
#     p1  2048     +32768    (16M) FAT32, bootable
#     p2  34816    +1843200  (900M) ext4  slot A
#     p3  1878016  +1843200  (900M) ext4  slot B
#     p4  3721216  ..end     ext4  /data
set -e
PAYLOAD="$1"
DISK=/dev/mmcblk0
W=/tmp/prov
log() { echo "[provision] $*"; }

[ -f "$PAYLOAD" ] || { echo "no payload: $PAYLOAD"; exit 1; }
rm -rf "$W"; mkdir -p "$W"
log "unpacking payload"
tar xzf "$PAYLOAD" -C "$W"
[ -d "$W/boot" ] && [ -f "$W/rootfs.tar.gz" ] || { echo "bad payload layout"; exit 1; }

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
sfdisk -R "$DISK" 2>/dev/null || true      # re-read partition table
sleep 1

log "formatting"
mkfs.vfat -n BOOT "${DISK}p1" >/dev/null
for p in p2 p3 p4; do mke2fs -t ext4 -F -q "${DISK}${p}"; done

log "writing boot (p1)"
mkdir -p /mnt/p1; mount "${DISK}p1" /mnt/p1
cp "$W"/boot/* /mnt/p1/
sync; umount /mnt/p1

log "writing rootfs to slots A + B"
mkdir -p /mnt/r
for p in p2 p3; do
    mount "${DISK}${p}" /mnt/r
    tar xzf "$W/rootfs.tar.gz" -C /mnt/r
    sync; umount /mnt/r
done

log "done"
sync
