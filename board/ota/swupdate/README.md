# SWUpdate delivery *(milestone M1b)*

The delivery half of A/B OTA: [SWUpdate](https://swupdate.org) receives a `.swu`
over its built-in web server and applies it to the **inactive** slot, then arms
the A/B switch (see [`../README.md`](../README.md) for the boot/rollback half).

## Flow (verified end-to-end)

```
.swu  --(web upload / swupdate-client)-->  SWUpdate
   → writes the inactive slot
   → sets U-Boot env: rootpart=<inactive>, upgrade_available=1, bootcount=0
   → reboot → boots the updated slot → ab-confirm makes it permanent
   (a broken update never confirms → U-Boot rolls back)
```

## Building SWUpdate (Buildroot)

The stock Buildroot SWUpdate config is minimal — it has only the `raw` handler and
**no** U-Boot bootloader or shell-script handler. Enable them in the SWUpdate
sub-config (`package/swupdate/swupdate.config`):

```
CONFIG_UBOOT=y              # U-Boot env interface (writes rootpart etc.)
CONFIG_SHELLSCRIPTHANDLER=y # run a script from the .swu
```

Also enable `BR2_PACKAGE_LIBUBOOTENV` so `fw_printenv`/`fw_setenv` exist.

## Runtime setup

- **Web server port** — the default is 8080, which collides with a typical app
  console. Override it (here: 8090):
  `echo 'SWUPDATE_WEBSERVER_ARGS="-r /var/www/swupdate -p 8090"' > /etc/swupdate/conf.d/10-mongoose-args`
- **Mount the FAT boot partition persistently** (`/mnt/p1` via fstab). SWUpdate's
  U-Boot bootloader (and `fw_setenv`) read the env from the `uboot.env` file there;
  without it SWUpdate fails at the end trying to write its state marker.

## sw-description

This SWUpdate build parses **JSON only** (no libconfig). The shell-script handler
lives in the `scripts` section, **not** `images`:

```json
{
  "software": {
    "version": "0.2.0",
    "scripts": [
      { "filename": "update.sh", "type": "shellscript" }
    ]
  }
}
```

Build the `.swu` (a `newc` cpio with `sw-description` **first**):

```sh
printf 'sw-description\nupdate.sh\n' | cpio -o -H newc > update.swu
```

Apply it: `swupdate-client -v update.swu` (or drag-and-drop on the web UI).

See [`sw-description`](sw-description) and [`update.example.sh`](update.example.sh).

## Both slots must be complete

A/B only works if **both** slots are complete, self-sufficient systems (each with
SWUpdate, `fw_setenv`, and the confirm service). If you bootstrap slot B by cloning
slot A, re-sync it after you finish installing tooling — a stale slot can boot but
can't confirm or update itself.

## Gotchas

- Busybox `cpio` often lacks create (`-o`) — build the `.swu` on your host.
- `-H crc` may be unsupported by busybox; `-H newc` works and SWUpdate accepts it.
- Payload here is a version marker to keep the example small. A real update writes
  a whole rootfs image to the inactive partition with the `raw` handler
  (`type: "raw"`, `device: "/dev/mmcblk0pN"`) — same pipeline, bigger payload.
