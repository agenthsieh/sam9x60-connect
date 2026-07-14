#!/bin/sh
# A/B boot confirm: reaching this point means the boot is healthy, so the current
# slot's update is good — clear the trial flags to make it permanent.
# Runs from ab-confirm.service late in boot (after a short settle delay).
#
# Requires fw_printenv/fw_setenv (libubootenv). The U-Boot env lives in the
# uboot.env file on the FAT boot partition, so mount it first.

mount /dev/mmcblk0p1 /mnt/p1 2>/dev/null

if [ "$(fw_printenv upgrade_available 2>/dev/null | cut -d= -f2)" = "1" ]; then
    fw_setenv upgrade_available 0
    fw_setenv bootcount 0
    logger "ab-confirm: healthy boot, update confirmed (slot $(cat /etc/ab_slot 2>/dev/null))"
fi

sync
umount /mnt/p1 2>/dev/null
