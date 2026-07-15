#!/bin/sh
# Flash the recovery FIT into QSPI at 0x200000, on the running board.
# Safe to run from a healthy SD boot: it does not touch the SD card, so the
# board still boots normally afterwards. The recovery installer only runs when
# the SD boot.bin is absent and the boot ROM falls through to QSPI.
#
# QSPI map:  0x000000 at91bootstrap | 0x040000 u-boot | 0x140000 env | 0x200000 FIT
# mtd0 is the whole 16 MB chip, 4 KB erase blocks. The env at 0x140000 sits
# below the FIT region, so erasing from 0x200000 up never disturbs it.
#
# Usage: ./flash-qspi.sh recovery.itb
set -e
FIT="${1:?path to recovery.itb}"
SZ=$(stat -c%s "$FIT"); BLKS=$(( (SZ + 4095) / 4096 ))
echo "flashing $FIT ($SZ bytes, $BLKS blocks) to /dev/mtd0 @ 0x200000"
flash_erase /dev/mtd0 0x200000 "$BLKS"
dd if="$FIT" of=/dev/mtd0 bs=4096 seek=512 conv=notrunc
sync
# verify
dd if=/dev/mtd0 bs=4096 skip=512 count="$BLKS" 2>/dev/null | head -c "$SZ" > /tmp/.rb
if [ "$(cksum "$FIT" | cut -d' ' -f1)" = "$(cksum /tmp/.rb | cut -d' ' -f1)" ]; then
    echo "OK: readback matches"
else
    echo "ERROR: readback mismatch" >&2; rm -f /tmp/.rb; exit 1
fi
rm -f /tmp/.rb
