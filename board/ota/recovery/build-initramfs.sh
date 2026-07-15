#!/bin/sh
# Build the recovery installer initramfs (cpio.gz).
#
# THE trap that cost the most time: busybox and swupdate in Buildroot are
# *dynamically* linked (interpreter /lib/ld-linux.so.3). If you copy only the
# app libraries and forget the C runtime, EVERY binary — /init, /bin/sh,
# /bin/busybox — fails execve with ENOENT (-2) and the kernel panics with
# "No working init found". The fix is to resolve the FULL dependency closure
# with readelf, not a hand-picked lib list.
#
# Usage: BR=<buildroot output dir> ./build-initramfs.sh
set -e
BR="${BR:?set BR to the buildroot output dir}"
TGT="$BR/target"
ROOT="$(mktemp -d)"
LIBSRC="$TGT/lib $TGT/usr/lib"

# --- lay down the installer userland -----------------------------------------
mkdir -p "$ROOT"/bin "$ROOT"/sbin "$ROOT"/usr/bin "$ROOT"/usr/sbin \
         "$ROOT"/lib "$ROOT"/dev "$ROOT"/proc "$ROOT"/sys "$ROOT"/etc \
         "$ROOT"/mnt "$ROOT"/www "$ROOT"/tmp
cp "$TGT/bin/busybox"            "$ROOT/bin/"
cp "$TGT/usr/bin/swupdate"       "$ROOT/usr/bin/"
cp "$TGT/usr/bin/swupdate-client" "$ROOT/usr/bin/" 2>/dev/null || true
cp "$TGT/usr/bin/fw_printenv"    "$ROOT/usr/bin/"
cp "$TGT/usr/bin/fw_setenv"      "$ROOT/usr/bin/"
cp "$TGT/usr/sbin/mke2fs"        "$ROOT/usr/sbin/" 2>/dev/null || true
cp "$TGT/usr/sbin/mkfs.vfat"     "$ROOT/usr/sbin/" 2>/dev/null || true
cp "$TGT/sbin/sfdisk"            "$ROOT/sbin/"     2>/dev/null || true
cp -r "$TGT/usr/share/swupdate/www/." "$ROOT/www/" 2>/dev/null || true
cp "$(dirname "$0")/init" "$ROOT/init"; chmod +x "$ROOT/init"

# --- recursive shared-library closure (the important part) -------------------
find_lib() { for d in $LIBSRC; do [ -e "$d/$1" ] && { echo "$d/$1"; return; }; done; }
needed()   { LC_ALL=C readelf -d "$1" 2>/dev/null | grep NEEDED | sed 's/.*\[\(.*\)\].*/\1/'; }
QUEUE="$(find "$ROOT"/bin "$ROOT"/sbin "$ROOT"/usr -type f)"
: > "$ROOT/.seen"
while [ -n "$QUEUE" ]; do
  NEXT=""
  for f in $QUEUE; do
    grep -qxF "$f" "$ROOT/.seen" && continue; echo "$f" >> "$ROOT/.seen"
    for n in $(needed "$f"); do
      [ -e "$ROOT/lib/$n" ] && continue
      src="$(find_lib "$n")"
      [ -n "$src" ] || { echo "WARN: missing $n (needed by $(basename "$f"))" >&2; continue; }
      cp -L "$src" "$ROOT/lib/$n"; NEXT="$NEXT $ROOT/lib/$n"
    done
  done
  QUEUE="$NEXT"
done
cp -L "$(find_lib ld-linux.so.3)" "$ROOT/lib/" 2>/dev/null || true   # dynamic linker
rm -f "$ROOT/.seen"

# --- belt-and-suspenders applet links (init runs before --install) -----------
for a in sh mount umount mkdir cat ln ls sleep echo mv sync ip; do
  ln -sf busybox "$ROOT/bin/$a"
done

# --- pack. DEVTMPFS_MOUNT=y in the recovery kernel auto-mounts /dev, so no
#     device nodes are needed and we can build the cpio unprivileged. ---------
OUT="${OUT:-inst-initramfs.cpio.gz}"
( cd "$ROOT" && find . | cpio -o -H newc 2>/dev/null | gzip -9 ) > "$OUT"
echo "wrote $OUT ($(stat -c%s "$OUT") bytes)"
rm -rf "$ROOT"
