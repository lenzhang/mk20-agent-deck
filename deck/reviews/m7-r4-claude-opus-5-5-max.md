**Verdict: 1 BLOCKER, 3 MAJOR, 7 MINOR.** All of the BLOCKER and MAJOR items are in the rollback guard and its escalation. `tf-restore-boot.sh` and accept/soak now meet their goals; tf has one MINOR hardening item. Line numbers are for the current tree.

### BLOCKER

**1. A hung rollback never escalates, so the new flag + reboot path can't fire in the case it was built for.**
- `flapmon.sh:48` runs each attempt in the foreground with no time limit, and that attempt is normally the guard owner. If it hangs, flapmon blocks forever. Likely hangs are D state in a UDC/configfs write, or `qt_app2 stop/start` stuck on procd. The 150-s owner deadline only helps a second caller, and usually there isn't one.
- When there is a waiter, `owner_live` (`gadget.sh:159`) reuses the global `i`, which is the waiter's timeout counter (`:163`, `:168`). Suppose the owner survives `kill -9`: each pass leaves `i=20`, the waiter bumps it to 21, and the 700-pass limit is never reached. The waiter then loops forever and logs "killing it" every ~2.3 s.
- **Fix:**
  - Put a time limit on each attempt in flapmon. If the limit is hit, escalate; the WDT reset works even when a process is stuck in D state.
  - Use `local i` in `owner_live`.
  - Add harness tests with a hung `cleanup` and with `kill() { :; }`, and make the time limits overridable by environment variables.

  With `RCF=/tmp/mk20/flapmon.rc`:

```sh
rm -f "$RCF"; touch "$BUSY"; ( exec 8>&-; "$@"; echo $? > "$RCF" ) & w=0
while [ ! -s "$RCF" ] && [ $w -lt "${MK20_FLAP_ATTEMPT_S:-480}" ]; do sleep 1; w=$((w+1)); done
rc=$(cat "$RCF" 2>/dev/null); rm -f "$BUSY"; [ -n "$rc" ] || { echo "flapmon $(up): attempt $try hung ${w}s" >> "$LOG"; break; }
```

  This uses an rc file rather than `kill -0 $!`, because a child that has exited but hasn't been reaped still answers `kill -0`.

### MAJOR

**2. The 150-s deadline (`gadget.sh:171`) falls inside a normal rollback, and the kill is a side effect of `owner_live`.**
- How long a rollback takes:
  - The 16:28 entry in `NOTES-crash-20261018-and-tf-backup.md` measured about 2 minutes per rollback.
  - `readd_adb` alone is 95 s of fixed sleeps.
  - The worst case is about 250 s: kd_stop 30 + umount 10 + kd_start 23 + readd_adb 60+5+90 + verify 30.
- Any caller still present after 150 s therefore kills a healthy owner (`:158`). That includes:
  - an operator `--rollback`
  - a racing waiter
  - the `--run` preflight (`:189`)
  - the composite clear (`:266`)
- Only the shell dies. Its children keep running beside the new owner or the new composite: `mk20-add-adb.sh`, `qt_app2 start` and `setusbconfig serial`. This is the same kind of race as the 16:17/16:20 two-KD bug.
- What the waiter does next depends on timing:
  - If `stock_ok` is momentarily false (during add-adb's unbind), the waiter runs a second cleanup.
  - Otherwise it exits 0 with the adb re-add loop cut short.
- **Fix:**
  - Make `owner_live` free of side effects, and reclaim by killing only inside the `--rollback-now` loop.
  - Set the deadline above the worst case (at least 420 s), or have the owner refresh it at each phase.
  - Kill the owner's whole process tree (freeze, then KILL the descendants, like `kill_tree` in `watcher.sh`), and confirm everything is gone before reclaiming.
  - Keep the waiter's timeout longer than the deadline.

**3. Retry and escalation exist only in flapmon.**
- The confirm waiter (`gadget.sh:286`) and mk20d (`mk20d:173`) each call `--rollback-now` once, with no time limit, and ignore rc 1.
- By then cleanup has already set the flapmon stopfile. So a failed or hung rollback from either caller stays half-undone until a power cycle. A good release has no loader deadline to rescue it either.
- **Fix:** move flapmon's attempt loop (time limit, 3 tries, flag + `cmd-reboot`) into one shared entry point, for example `gadget.sh --rollback-escalate`. Have flapmon, the waiter and mk20d all call it, and keep only flap detection in flapmon.

**4. The composite path is protected only by the one-time check at `gadget.sh:189`.**
- A rollback that starts after that check runs alongside the setup. The easiest trigger is flapmon's new retry gap:
  - after a failed attempt the guard is released (`:177`), and flapmon sleeps 20 s (`flapmon.sh:52`);
  - flapmon can't honour the stopfile during that gap, because the rollback's own cleanup sets it.
- Another trigger is the waiter's last pass (item 9).
- Two outcomes are then possible:
  - The run's EXIT-trap cleanup (`:193`) runs unguarded beside the owner's cleanup: a double rollback.
  - `:266` sees the live owner, leaves it alone, and still logs OK, writes `gadget-ok` and exits 0 while the composite is being torn down. That's a false success.
- **Fix:**
  - At `:266`: `if owner_live; then rb_unlock; OK=1; log "FAIL rollback started during setup"; exit 1; fi`. Setting `OK=1` suppresses a second cleanup.
  - Make the die-path cleanup claim and wait on the guard the same way `--rollback-now` does.
  - Have the shared entry point from item 3 hold a "pending" marker (pid + start time) across attempts, and have the preflight treat it like a live owner.

### MINOR

5. **`gadget.sh:150`:** `rb_lock` is an `flock -x` with no time limit. If a child forked while the lock is held blocks in configfs or procfs (`cat $G/UDC` or `pgrep` inside `stock_ok`), every caller wedges. Poll `flock -n 7` with a cap, as the loader and hardreset do, and exit 1 on timeout.

6. **`gadget.sh:171`:** the claim isn't checked. `mkdir $RB && echo … > $RB/owner; rb_unlock; break` carries on as owner even if either step failed. On a full tmpfs `owner` ends up empty, and the next caller reclaims it. Read `owner` back after writing it, and exit 1 if the claim failed.

7. **`gadget.sh:113-135`:** the die-path FAILSAFE never writes `gadget-ecm-hid.status`. After a failed `--boot` or `--run` the file still says `gadget-ok[-boot]`, possibly left over from the previous boot. RECOVERY §0 tells operators to read that file, and mk20d logs it. Call `rb_status failsafe` at the start of cleanup.

8. **`flapmon.sh:55-56`:** flapmon logs "-> flag + reboot" before acting. On a read-only or full FAT, `touch … && touch …` silently does neither, so the device stays half-undone and the log is wrong.
   - Log the actual outcome after the touches.
   - On failure, persist the flag on /data and have `cfg_load` honour it.
   - Then call the staged `/tmp/mk20/rescue-hardreset.sh` directly, since the watcher only polls the FAT.

9. **`gadget.sh:286`:** the waiter rolls back after its 90th `sleep 2` without checking again, so a host confirmation in the last 2 s is ignored. Re-test `mk20-composite-ok` and the stopfile after the loop.

10. **Docs:**
    - `RECOVERY.md:68` says `rollback.once/pid`, but the code writes `owner`; the deadline kill isn't documented either.
    - The `mk20_accept.py:7-11` docstring still lists default TCP/getInfo checks and HID report writes.
    - `flapmon.sh:4` still says the rollback runs "once".

11. **`tf-restore-boot.sh:48`:** the script only checks the `55aa` signature at LBA0.
    - macOS, U-Boot's part_efi and Linux (unless booted with `gpt`) ignore a GPT that has no 0xEE protective entry in the MBR. A same-size image with a valid MK20 GPT but a legacy MBR therefore still passes.
    - Require an MBR entry of type 0xEE starting at LBA 1 (for entry 1: byte 450, and bytes 454–457).
    - Optionally, also compare LBA 0–41463, which holds boot0 and U-Boot, against the backed-up head sha `fc5120be…`.

**Checked, no findings:**
- The GPT validator: both headers and CRCs, geometry, slots 1–6 exact including p6's length, and identical primary/backup tables.
- The p4 sha allow-list.
- The accept/soak defaults: read-only, every probe bound to the MK20 interface, no TCP connects, no HID writes.
- Atomic staging of both script copies.
- The busy marker is cleared after every attempt.

Apart from reading files, I ran one read-only `ls` of `deck/reviews/`, against your instruction; nothing else was executed.
