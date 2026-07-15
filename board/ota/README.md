# board/ota — A/B OTA *(milestone M1)*

Redundant **A/B rootfs** on the SD card with **automatic rollback**, driven by the
U-Boot environment. Needs nothing more exotic than a scriptable U-Boot env — it
even works on a U-Boot built *without* `setexpr`.

> When the SD card itself is blank or broken, the A/B scheme has nothing to fall
> back to. That last-resort case is handled by the **[QSPI recovery installer
> (M2)](recovery/README.md)**, which boots from flash and can reinstall the system.

## Partition layout

```
p1  FAT   boot   (bootstrap + U-Boot + env + kernel FIT)
p2  ext4  rootfs slot A
p3  ext4  rootfs slot B
p4  ext4  /data   (persists across updates)
```

Create p3/p4 in the free space after p2 (e.g. `sfdisk --append`), then
`mkfs.ext4` them. Slot B starts as a copy of slot A.

## How slot selection works

U-Boot env variables:

| var | meaning |
|-----|---------|
| `rootpart` | active rootfs partition (`2`=A, `3`=B) |
| `bootcount` | boot attempts of a slot currently on trial |
| `bootlimit` | max attempts before rollback (default `3`) |
| `upgrade_available` | `1` while a freshly-written slot is on trial |

Boot flow — `bootcmd` runs `run ab_select; run ab_setargs; <board's existing boot chain>`:

- **`ab_select`** — if a trial is in progress (`upgrade_available=1`) and
  `bootcount` reached `bootlimit`, roll back to the other slot and clear the
  flags; otherwise increment `bootcount`.
- **`ab_setargs`** — builds `bootargs` with `root=/dev/mmcblk0p${rootpart}`
  (expanded at run time, so the kernel gets the real partition).

On a **healthy** boot, `ab-confirm.service` clears `upgrade_available`+`bootcount`,
making the new slot permanent. An **unhealthy** slot never reaches confirm, so
`bootcount` climbs on each (re)boot until U-Boot rolls back to the known-good slot.

An update therefore looks like: write the inactive slot → set `rootpart`=that slot,
`upgrade_available=1`, `bootcount=0` → reboot → healthy? confirm makes it permanent;
broken? auto-rollback.

## Files

| file | what |
|------|------|
| `setup-ab-env.sh` | one-time: write the A/B variables into the U-Boot env |
| `ab-confirm.sh` | clears the trial flags on a healthy boot |
| `ab-confirm.service` | runs `ab-confirm.sh` late in boot |

## Requirements

- **libubootenv** (`fw_printenv`/`fw_setenv`) with `/etc/fw_env.config` pointing at
  the U-Boot environment. Here the env is the `uboot.env` file on the FAT boot
  partition, so `fw_env.config` is `\/mnt\/p1\/uboot.env 0x0000 0x4000` (FAT mounted
  at `/mnt/p1`).
- The rootfs must mount `/` from the kernel `root=` cmdline (fstab uses
  `/dev/root`), so changing `rootpart` is enough — no per-slot fstab edits.

## Gotchas (learned the hard way)

- **No `setexpr`?** Don't do arithmetic. Increment with ordered string compares,
  high→low, so a single boot bumps the counter exactly once (see `ab_select`).
- **`systemctl mask` won't mask a unit that already has a real file** in
  `/etc/systemd/system` — use `disable` to stop it starting.
- Both slots must be *complete* (tooling + confirm service). If you clone slot A
  to slot B before finishing setup, re-sync the missing bits.
