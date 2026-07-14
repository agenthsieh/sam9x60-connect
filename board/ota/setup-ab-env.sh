#!/bin/sh
# One-time setup of the U-Boot A/B environment.
# Requires fw_setenv (libubootenv) and a configured /etc/fw_env.config.
#
# NOTE: `ab_setargs` and `bootcmd` below embed *this board's* existing boot chain
# (SAM9X60 Curiosity: the `at91_*` calls and the console/cma/pm bootargs). Adapt
# those pieces to your board — keep the `run ab_select; run ab_setargs;` prefix.
set -e
FW="${FW:-fw_setenv}"

# --- slot state -------------------------------------------------------------
$FW rootpart 2            # active rootfs partition: 2 = slot A, 3 = slot B
$FW bootcount 0
$FW bootlimit 3
$FW upgrade_available 0

# --- build bootargs with the active slot's rootfs (expanded at run time) ----
$FW ab_setargs 'setenv bootargs console=ttyS0,115200 root=/dev/mmcblk0p${rootpart} rw rootwait cma=32m rootfstype=ext4 atmel.pm_modes=standby,ulp0'

# --- A/B selection + rollback ----------------------------------------------
# Chained, ordered (high->low) string-compare increment — no `setexpr` needed.
$FW ab_select 'if test "${upgrade_available}" = "1"; then if test ${bootcount} -ge ${bootlimit}; then setenv upgrade_available 0; setenv bootcount 0; if test "${rootpart}" = "3"; then setenv rootpart 2; else setenv rootpart 3; fi; saveenv; else test "${bootcount}" = "2" && setenv bootcount 3; test "${bootcount}" = "1" && setenv bootcount 2; test "${bootcount}" = "0" && setenv bootcount 1; saveenv; fi; fi'

# --- run A/B logic first, then the board's existing boot chain --------------
$FW bootcmd 'run ab_select; run ab_setargs; run at91_set_display; run at91_pda_detect; run at91_prepare_video_bootargs; run at91_prepare_bootargs; run at91_prepare_overlays_config; run bootcmd_boot;'

echo "A/B U-Boot env configured."
echo "Verify: fw_printenv | grep -E 'rootpart|bootcount|bootlimit|upgrade_available|ab_|^bootcmd='"
