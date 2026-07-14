#!/bin/sh
# Example OTA payload run by SWUpdate's shellscript handler.
# Applies a change to the INACTIVE A/B slot, then arms the switch.
# (The FAT boot partition is assumed persistently mounted, so fw_setenv works.)

CUR=$(mount | grep " / " | grep -oE "mmcblk0p[0-9]")
if [ "$CUR" = "mmcblk0p2" ]; then INACT=3; else INACT=2; fi

mkdir -p /mnt/inact
mount /dev/mmcblk0p${INACT} /mnt/inact 2>/dev/null
# --- put your real update here (write files, or dd a rootfs image) ---
echo "0.2.0" > /mnt/inact/etc/ota-version
# ---------------------------------------------------------------------
sync
umount /mnt/inact 2>/dev/null

# arm the A/B switch (U-Boot rolls back if the new slot never confirms)
fw_setenv rootpart ${INACT}
fw_setenv upgrade_available 1
fw_setenv bootcount 0
logger "OTA: staged slot p${INACT}, armed A/B switch"
