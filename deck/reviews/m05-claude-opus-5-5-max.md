I'll read the M0.5 diff, the five scripts, and the plan section, then review.Now the plan's §11.I have all five scripts and the plan. Checking a few related files (the lunch stub, the review prompt, other scripts that might take the lock) to confirm paths and how things get started.The stub starts the watcher with stdio on `/dev/null`, but jobs write to FAT logs. Now checking whether the gadget script leaves daemons (bridge, flapmon, adbd) holding those FAT log descriptors.Hardware notes confirm the 16:49 remount got EBUSY ("files open"). I'll do one last portability sweep for `flock -w`, `pkill`, `setsid`, `nohup` and `timeout` across the device scripts.There's a second copy at `deck/firmware/rescue/hardreset.sh`; checking whether it matches so the findings carry over.## M0.5 review: one BLOCKER, two MAJOR

The v2.1 lock polling itself is correct. `flock -n`, `kill -0`, `sleep 0.5` and `${h#????????????}` all work on BusyBox 1.27.2. None of the five files can reach `reboot` or sysrq any more.

### BLOCKER

**B1. `postswap-check.sh:15`: the revert writes p4 without the lock.**
- It runs `flock -n 8 || log "…writing anyway"`.
- `hardreset.sh` holds the lock from when it gets it, through `stop:true` and its 40 s sleep.
- The boot-once revert and the detached `--revert-timeout` revert both run alongside the watcher's main loop. If either overlaps a cmd-reboot, a 6 MiB `dd` runs with a watchdog reset due within 16 s. That tears p4, and recovery needs a TF pull.

*Fix:*
- Poll the lock (up to 60 s). If it's still busy, don't write: keep the re-arm and exit 1.
- After getting the lock, require `ubus call system watchdog` to report `"running"` before the `dd`. This also covers a hardreset killed right after `stop:true`.
- Add the same check before `bootswap.sh:53`.

### MAJOR

**M1. `hardreset.sh:27`: the read-only remount can never succeed.** The kernel refuses `remount,ro` (EBUSY) while any file on that filesystem is open for writing. Some always are:
- hardreset inherits its caller's stdout, which is on the FAT: `job.log` from bootswap (`watcher.sh:45`), or the postswap job log.
- The bridge loop keeps `gadget-ecm-hid.log` open for its whole uptime (`gadget-ecm-hid-adb.sh:170`).

So the EBUSY you saw at 16:49 happens on every reset, and every reset still stops the FAT uncleanly.

*Fix:*
- Callers run `exec >/tmp/mk20/<job>.out 2>&1` before calling hardreset. hardreset starts with `exec </dev/null >/dev/null 2>&1`.
- Send gadget line 170's output to `/dev/null` and append to the log one line at a time, as flapmon does.
- Before the remount, log which `/proc/*/fd/*` still point into `/mnt/SDCARD` to `/tmp`.

**M2. `postswap-check.sh:12,19`: the revert runs its steps in the wrong order.**
- It removes the boot-once re-arm before writing. A reset before the `dd`, or the deferral from the B1 fix, then boots the rejected kernel with no auto-revert.
- It resets even after `RESTORE READBACK BAD`, so the board boots a p4 that is neither image. `bootswap.sh:56` correctly says DO NOT REBOOT in the same situation.

*Fix:* remove the re-arm only once `R = PS`. If the readback is bad, retry the `dd` once; if it's still bad, stay up with status `…DO-NOT-POWER-OFF`. Bootswap's own stock restores (lines 53 and 56) are never read back either; add a readback there too.

### MINOR

- **`hardreset.sh:17,26,30`:** `sync` before the stop has no time limit. Line 30 can also wait behind a slow remount, so "can never block the reset" is false; a wedged TF card means no reset. *Fix:* once the lock is held, start `( exec 9>&- >/dev/null 2>&1; sleep 12; ubus call system watchdog '{"stop":true}'; sleep 30 ) &`. It keeps fd 8, so the lock stays held until the reset.
- **`hardreset.sh:40`:** the `remount,rw` runs unconditionally and can undo the kernel's `errors=remount-ro` on a corrupted FAT. *Fix:* only remount if `/tmp/mk20/hr-ro` exists at that point.
- **`hardreset.sh:20`:** also refuse if `"magicclose":true` (procd would disarm the watchdog) or `timeout` ≥ 40 (the fallback re-enables pings before it fires). Re-check after the lock wait.
- **fd leaks:**
  - The remount subshell inherits fd 8, so a wedged remount keeps `kwrite.lock` after rc 4.
  - `watcher.sh:93` passes fd 9 into hardreset.
  - *Fix:* add `8>&- 9>&-` and `9>&-` respectively.
- **`watcher.sh:93`:** the call is synchronous, so the rescue loop ignores cmd-ping and job.sh for up to about 645 s. Cap the wait at about 120 s, or run it in the background.
- **`watcher.sh:46`:** the job timeout kills only the job's shell. An orphaned p4 `dd` finishes, but the readback and postswap staging never run. The whole-group kill planned in §11 must skip jobs holding `kwrite.lock`.
- **Exit codes:** rc 2 is also what ash returns for a failed `exec 8>` or a syntax error; use 10 and up. `bootswap.sh:65` and `kernel-inventory.sh:20` return 0 on failure.
- **Test mode:** with `MK20_ROOT` set, hardreset still calls the real `ubus`, so running the harness on the box resets it. Use `UBUS=${MK20_UBUS:-ubus}` and refuse if it isn't overridden.
- **FAT:**
  - `hardreset.log` and `postswap.log` have no size cap, and postswap writes each line twice (`tee` plus the job log).
  - `bootswap.sh:40` deletes the stock copy before checking free space, and rewrites 6.2 MiB on every stock→patched swap. Skip that when `sha256sum $STOCK` already equals PSHA.
- **`kernel-inventory.sh`:**
  - Line 9: check the sha of `boot.img.tmp` before the `mv`.
  - Line 12: also drop ALLOWLIST lines with the same name, or a rebuilt image leaves a stale entry.
  - Assert each image is exactly the p4 size, because line 18 hashes the whole partition.
- **Leftovers:**
  - `watcher.sh:3,91` still mention a reboot fallback.
  - `reboot -f || reboot` is still in `lunch-boot-swap.sh:55,160` and `boot-swap-mark-fail.sh:11`, which would hang in PSCI if reused.
  - `deck/firmware/rescue/hardreset.sh` is an identical copy and needs the same fixes.