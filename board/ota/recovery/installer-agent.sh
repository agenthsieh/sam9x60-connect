#!/bin/sh
# USB-serial agent for the QSPI recovery installer — busybox only (no Python in
# the installer initramfs). Speaks the same JSON control protocol the NAS hub
# uses, so the hub's "push a .swu over USB" path drives provisioning too.
#
#   host -> installer  {"t":"put","name":"<f>","size":<S>}  then <S> raw bytes
#                      {"t":"apply","name":"<f>"}            provision from <f>
#   installer -> host  {"t":"inst","sd":"blank|ok","ready":1}   heartbeat, 2 s
#                      {"t":"put_result","ok":<bool>,...}
#                      {"t":"apply_result","ok":<bool>,"msg":"…"}
#
# RAW mode on the tty is essential — cooked mode eats control bytes (XON/XOFF,
# EOF) inside binary payloads and silently truncates the transfer.
PORT=/dev/ttyGS0
SPOOL=/tmp/ota
mkdir -p "$SPOOL"

for i in 1 2 3 4 5; do [ -e "$PORT" ] && break; sleep 1; done
stty -F "$PORT" raw -echo 2>/dev/null
exec 3<>"$PORT"

sd_state() { [ -b /dev/mmcblk0p2 ] && echo ok || echo blank; }

# heartbeat so the hub can tell the board is in installer mode
( while true; do printf '{"t":"inst","sd":"%s","ready":1}\n' "$(sd_state)" >&3; sleep 2; done ) &

while true; do
  # tolerate EOF/errors — the tty returns EOF when the host isn't attached yet;
  # without this guard the loop would exit and drop to the rescue shell.
  IFS= read -r line <&3 || { sleep 0.2; continue; }
  case "$line" in
    *'"t":"put"'*)
      size=$(printf '%s' "$line" | sed -n 's/.*"size":\([0-9]*\).*/\1/p')
      name=$(printf '%s' "$line" | sed -n 's/.*"name":"\([^"/]*\)".*/\1/p')
      [ -z "$name" ] && name=upload.bin
      head -c "$size" <&3 > "$SPOOL/$name"
      got=$(wc -c < "$SPOOL/$name")
      if [ "$got" = "$size" ]; then
        printf '{"t":"put_result","ok":true,"name":"%s"}\n' "$name" >&3
      else
        printf '{"t":"put_result","ok":false,"err":"size %s!=%s"}\n' "$got" "$size" >&3
      fi ;;
    *'"t":"apply"'*)
      name=$(printf '%s' "$line" | sed -n 's/.*"name":"\([^"/]*\)".*/\1/p')
      if /sbin/provision.sh "$SPOOL/$name" >/tmp/prov.log 2>&1; then
        printf '{"t":"apply_result","ok":true,"msg":"provisioned; rebooting"}\n' >&3
        sync; sleep 1; reboot -f
      else
        printf '{"t":"apply_result","ok":false,"msg":"%s"}\n' \
          "$(tail -1 /tmp/prov.log 2>/dev/null | tr -d '\"\\')" >&3
      fi ;;
    *'"t":"restore"'*)
      # put the SD boot.bin back and reboot into the normal system (no card reader)
      mount /dev/mmcblk0p1 /mnt 2>/dev/null
      if mv /mnt/boot.bin.off /mnt/boot.bin 2>/dev/null; then
        sync; printf '{"t":"restore_result","ok":true}\n' >&3
        umount /mnt 2>/dev/null; sleep 1; reboot -f
      else
        printf '{"t":"restore_result","ok":false}\n' >&3
      fi ;;
    *'"t":"ping"'*) printf '{"t":"pong"}\n' >&3 ;;
  esac
done
