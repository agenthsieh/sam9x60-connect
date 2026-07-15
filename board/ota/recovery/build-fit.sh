#!/bin/sh
# Package the recovery installer as a U-Boot FIT image.
#
# Two hard-won rules:
#  1. Use the FULL kernel, not a slimmed one. A stripped-down kernel hangs
#     silently right after "Loading compiled-in X.509 certificates"; the stock
#     zImage that boots the A/B slots boots the installer too.
#  2. Load addresses must not overlap each other OR the address the bootloader
#     reads the FIT into. On the 128 MB SAM9X60-D1G (DDR 0x20000000-0x28000000):
#
#        FIT blob (sf read dest)  0x24000000   (~13 MB window)
#        kernel                   0x25000000
#        ramdisk                  0x26000000
#        fdt                      0x27000000
#
#     The self-decompressing zImage unpacks low (~0x20008000), well clear of all
#     of the above.
#
# Usage: ./build-fit.sh <zImage> <dtb> <initramfs.cpio.gz> [out.itb]
set -e
KERNEL="${1:?zImage}"; DTB="${2:?dtb}"; RAMDISK="${3:?initramfs.cpio.gz}"
OUT="${4:-recovery.itb}"
ITS="$(mktemp)"
cat > "$ITS" <<EOF
/dts-v1/;
/ {
    description = "SAM9X60 QSPI recovery installer";
    #address-cells = <1>;
    images {
        kernel  { data = /incbin/("$KERNEL");  type = "kernel";   arch = "arm"; os = "linux"; compression = "none"; load = <0x25000000>; entry = <0x25000000>; hash-1 { algo = "crc32"; }; };
        fdt     { data = /incbin/("$DTB");      type = "flat_dt";  arch = "arm";              compression = "none"; load = <0x27000000>;                       hash-1 { algo = "crc32"; }; };
        ramdisk { data = /incbin/("$RAMDISK");  type = "ramdisk";  arch = "arm"; os = "linux"; compression = "gzip"; load = <0x26000000>;                       hash-1 { algo = "crc32"; }; };
    };
    configurations { default = "conf"; conf { kernel = "kernel"; fdt = "fdt"; ramdisk = "ramdisk"; }; };
};
EOF
mkimage -f "$ITS" "$OUT" >/dev/null
rm -f "$ITS"
SZ=$(stat -c%s "$OUT")
echo "wrote $OUT ($SZ bytes)"
[ "$SZ" -lt 13631488 ] || echo "WARN: FIT larger than the 13 MB QSPI read window — enlarge BOOTCOMMAND sf read length"
