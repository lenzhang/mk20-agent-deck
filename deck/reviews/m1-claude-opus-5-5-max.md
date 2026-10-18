I'll read the M1 diff first, then the rescue scripts, daemon, build tool, tests, and the plan's requirements section.The diff gives me the whole M1 picture; now I'll read the on-disk files for exact line numbers, plus the plan's §11.On-disk files match the diff. Reading the plan's §11 and the surrounding requirement sections next.Plan read. I'll check the review prompt and the legacy scripts (bootswap/postswap, boot-every) that interact with the rescue tier.Reading the legacy scripts that share locks/paths with the new rescue tier.I have the full picture of the state machine. Before writing up, I'll pin exact line numbers in `gadget.sh` and the tests that I plan to cite.## M1 review: not ready to merge

There is one BLOCKER in the loader's state machine, plus several paths that cause a reset loop or lose remote recovery. Payload verification holds up: files are copied to tmpfs and re-hashed, expansions are quoted, and slot fields are parsed strictly. I found no BusyBox 1.27 incompatibilities in the device scripts.

### BLOCKER

**B1. A failed or never-tested release can run as `current` with no probation.** If that release breaks USB, KD and adb stop working, so the watcher can't receive commands.
- **Unconfirmed first install.** On boot 2, `rollback_to_good` deletes `pending` (`loader.sh:93`) and the loader exits 11. Slot A still points at the release, so boot 3 onward runs it with no deadline. The same happens when rollback fails (no `good`) or fails silently (the `mv` in `slot_write` is unchecked, `:55`).
- **Power cut** between `slot_write` and `wstate pending` (`:148-149`), or between `rm pending` and `slot_write` (`:93-95`).
- **Auto-rollback leaves the failed release valid in the other slot.** Two things can promote it later: `cmd-rollback` (`:153` takes the other slot, not `good`), or a corrupt MANIFEST in the newer slot. Manual rollback also leaves `good` pointing at the release you rejected.

**Fix:**
- In `switch`, write `pending` and `attempts` before `slot_write`.
- In `rollback_to_good`, write the slot and check `mv` before clearing `pending`.
- At boot, drop `pending` if it ≠ CUR or = `good`.
- Launch a non-pending CUR only if it equals `good`; otherwise exit 11.
- Make manual rollback target `good`.

### MAJOR

**M1. Reset loops.**
- **Command files:** `watcher.sh:117,122` never check that `rm -f cmd-reboot` / `cmd-rollback` succeeded. If the FAT is read-only (vfat defaults to `errors=remount-ro`), every boot resets soon after the watcher starts, usually before adb is up. `runjob` (`:60`) has the same flaw and re-runs `job.sh` on every poll. **Fix:** act only if `rm -f "$f" && [ ! -e "$f" ]`.
- **State writes:** `loader.sh:107,134,139` ignore `wstate` failures. On a read-only `/data`, attempts never reaches 2 and the deadline marker never sticks, yet `confirm` still exits 0. Every boot then ends in a watchdog reset at 600 s. **Fix:** exit 11 instead of launching, skip the reset, and make `confirm` fail.

**M2. Deadline path.**
- `loader.sh:135` runs the unverified FAT `hardreset.sh`, which §11 moves off the FAT. If the file is missing, no reset ever happens. **Fix:** use the verified rescue copy.
- The deadline counts from boot, but boot-once jobs (up to 300 s each, `watcher.sh:86-90`) run before the loader. **Fix:** use max(600, launch + 480).
- When the self-test fails, `mk20d:42` only waits for that reset. hardreset refuses exactly when the watchdog isn't armed, which is one of the self-test checks. **Fix:** restore stock ACM+KD+adb immediately (`gadget.sh --rollback-now`).

**M3. Boot stub** (`lunch.sh:10-19`). If the ext4 watcher passes its hash check but is broken, the FAT fallback never runs. That leaves no watcher, no loader and no adb. **Fix:** add a detached ~60 s check that the main loop is running; if not, kill it and start the FAT watcher.

**M4. Job timeout** (`watcher.sh:64-65`). `kill_tree` can kill a bootswap `dd` that holds `kwrite.lock`, leaving the p4 boot-partition write half done. **Fix:** don't kill while `flock -n` on that lock fails.

**M5. FAT space.** With 1.36 MiB free, these logs have no size cap: `loader.log` (which also gets mk20d's stdout), `mk20d.log`, `gadget-ecm-hid.log` and `hardreset.log`. The bridge loop also keeps a FAT file open for writing (`gadget.sh:170`), so hardreset's remount-ro fails with EBUSY whenever composite mode is up. A full or dirty FAT breaks the command channel and makes M1 more likely. **Fix:** write logs to `/data`, with caps.

### MINOR

- **Kernel gate bypass** (`loader.sh:69`): `kernels=any` passes, and so does an empty `p` (failed p4 read) with an empty `kernels=`. **Fix:** require `is_hex64 "$p"` and drop `any`.
- **MANIFEST checks:**
  - `:79` hashes whatever the path is, and the loader has no timeout, so a FIFO hangs it. Hash only `-f && ! -h` files.
  - `:86` checks the source `sbin/mk20d`, not the verified copy.
  - `:78` allows setuid modes.
  - **Fix:** parse one tmpfs snapshot of MANIFEST, checked against the slot's `manifest_sha`.
- **Version/hash regex** (`:23-24`): `grep` matches line by line, so `$'x\n1.0.0'` passes. **Fix:** use `case`.
- **Exit 11 runs the legacy boot-every composite** (`watcher.sh:94`). That contradicts §11, which says an unknown kernel gets no USB changes. **Fix:** return a distinct code and run only adb.
- **No loader lock.** A second `boot` counts attempts again and deletes the live `$T/rel`. **Fix:** add `flock -x` and a per-boot marker. Separately, `switch` doesn't refuse while a kernel swap is pending.
- **Self-test samples once.** A flap rollback in progress stops KD, so the self-test reads FAIL; §11 calls this "degraded". **Fix:** retry before failing.
- **Smaller items:**
  - `watcher.sh:33` overwrites the copy before verifying it; copy to `.tmp`, then `mv`.
  - `:118,124` run a `sync` with no time limit; drop it, since hardreset bounds its own.
  - `gadget.sh:44` queued jobs lose `MK20_REL`; prefix it.

### Test gaps

- **No fault injection**, although it's an M1 deliverable. Add a `sync`/`mv` shim that kills at the Nth call across switch, boot, confirm and rollback. Assert that the next boot is `good` or on probation.
- **Loader tests:**
  - `test_loader.py:61` stops at boot 2; boot 3 would expose B1.
  - `:99` asserts the very fallback B1 should forbid.
- **Untested areas:**
  - watcher main loop, mk20d, `gadget.sh` (no fake configfs)
  - the deadline-to-hardreset path
  - read-only `/data` or FAT
  - MANIFEST negatives: absolute path, symlink, FIFO, duplicate entries, `any`
- **Wrong BusyBox under test.** Tests use the host's busybox and util-linux flock instead of 1.27.2. **Fix:** pin 1.27.2, and lint for `setsid|nohup|pkill|timeout`.