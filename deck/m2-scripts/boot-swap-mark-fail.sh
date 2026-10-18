#!/bin/sh
# DEPRECATED (2026-10-18): uses reboot, which hangs in PSCI on this board. Do not reuse; see firmware/rescue/hardreset.sh.
# Mark boot-swap failed so next lunch/boot restores stock from SDCARD.
touch /mnt/SDCARD/boot-swap-fail
# Re-arm lunch with the backup copy of the swap script if present
if [ -f /mnt/SDCARD/lunch.sh.boot-swap.bak ] && [ ! -f /mnt/SDCARD/lunch.sh ]; then
  cp /mnt/SDCARD/lunch.sh.boot-swap.bak /mnt/SDCARD/lunch.sh
  chmod +x /mnt/SDCARD/lunch.sh
fi
sync
echo "marked fail; reboot to restore stock"
reboot -f 2>/dev/null || reboot
