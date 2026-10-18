**Round 7 review: MK20 fd-7 transition mutex (setup/rollback), diff c502516..d2d1ece**

The r5 changes do what they say. The main problem is one new BLOCKER: queued `--run` and `--rollback` jobs run under the watcher, which kills the whole job tree at 300 s. The new 450 s / 330 s / 900 s bounds are all longer than that, so the owner-local supervisors never get to act on that path. There is also one new MAJOR (the generation check fails open), and all four known-open items are confirmed. I didn't run anything. I did read `watcher.sh`, `loader.sh` and the full `mk20-add-adb.sh`, because the BLOCKER depends on them.

**Verified OK**
- **#4:** both double forks are correct. `mk20-add-adb.sh` runs `killall adbd` before starting adbd and never uses `$!`, so detaching can't cause duplicate adbd processes or a lost pid.
- **#9:** every long-lived process started under the mutex now closes fd 7: both adbd starts, the bridge loop, flapmon, the confirm waiter, KD (procd or fallback), `$KDSVC stop/start`, `setusbconfig` and add-adb.
- **#7:** `composite.gen` is written by `setup_main` under the mutex and read only after `tl_lock`, so check-and-act is atomic against setup. flapmon and both waiter attempts carry `$GEN`, and rc 5 is terminal in both.
- **#6:** keeping the marker on rc 4 is right for the case it targets (another owner is still running).
- **#5:** the bounds are consistent with each other: 900 s is more than the ~830 s worst-case setup hold.
- **mk20d retry:** 0 and 3 are terminal; anything else gets one retry, then the flags are written. Same contract as the waiter.

**BLOCKER**

1. **The watcher kills queued transitions before their own bounds fire** (`gadget.sh:66`, `:171-174`; `rescue/watcher.sh:75-78`, `:31-37`).
   - **How:** `runjob` caps every job at `MK20_JOB_TIMEOUT=300`, then STOPs, TERMs, and 2 s later KILLs the whole tree, owner included. So "no caller ever kills another owner" (`gadget.sh:144`) is false for the watcher. The r6 setup supervisor only protects the mk20d `--boot` path.
   - **Setup:** with SUT=450, the timeout branch (`:244-254`) can never run for a queued `--run`. Take a hung `$KDSVC`, `setusbconfig` or add-adb, which is what these bounds exist for. TERM triggers `trap 'exit 1' TERM`, so the EXIT-trap cleanup starts. The KILL 2 s later stops it right after it has unbound the UDC and stopped KD. The result is no USB, no KD, no verification and no escalation: the mutex is freed, but the board needs a power-cycle. With the old SUT=300 this was a race; now it always happens.
   - **Rollback:** lock wait (≤900 s) + cleanup (≤330 s) + verify (30 s) is more than 300 s. With lock contention, the kill can land between `kd_stop` and `kd_start`. Without contention, a slow rollback that succeeded is killed in `readd_adb` and reported as TIMEOUT.
   - **Fix:** have the queued job detach the transition and not hold the FAT job log open:
     ```sh
     echo "( MK20_REL=${MK20_REL:-} MK20_GADGET_LOG=$LOG sh $(readlink -f "$0") $A </dev/null >/dev/null 2>&1 & ); echo started" > $Q.tmp
     ```
     A detached manual rollback has no supervisor, so this also needs MAJOR #3.

**MAJOR**

1. **New: the generation check fails open** (`gadget.sh:259`, `:201-202`).
   - **Missing file:** the write is unchecked. If it fails (tmpfs full) or the file disappears, `""` counts as a mismatch. Every flapmon or waiter rollback of the live composite then returns 5, and both exit 0. A flapping or unconfirmed composite stays up with no monitor and no escalation.
   - **Newer composite not up:** rc 5 is also returned when the newer generation isn't actually running (its setup failed and the failsafe didn't reach stock). That blocks a repair that would have helped.
   - **Fix:** move the write below `die()` (line 265): `{ echo "$GEN" > "$GENF.t" && mv -f "$GENF.t" "$GENF"; } || die "gen write"`. In the rollback, check `stock_ok` → exit 0 first. Then set `cur=$(cat "$GENF" 2>/dev/null)` and return 5 only if `[ -n "${MK20_GEN:-}" ] && [ -n "$cur" ] && [ "$cur" != "$MK20_GEN" ] && [ -e $C/ecm.usb0 ]`.

2. **Known-open, confirmed: `bounded_wait` only tracks the root** (`gadget.sh:178-187`).
   - **Who can get stuck holding fd 7:** cleanup's external commands that don't close fd 7, namely configfs `rmdir` (121) and `umount` (128). In setup, also `insmod`, `mount` and `ifconfig`.
   - **What happens:** when the root dies, the result is rc 1. The owner reports 0 or 1 and exits while the grandchild still holds the mutex, so everyone after it gets rc 4. Supervised callers escalate only after 2–3 × 900 s. A manual rollback, or a setup timeout that did reach stock, never escalates.
   - **Fix:** wait for every pid that was signalled.
     ```sh
     kill_tree() { kill -STOP "$1" 2>/dev/null; echo "$1"; for k in $(kids "$1"); do kill_tree "$k"; done; kill -9 "$1" 2>/dev/null; }
     # bounded_wait, replacing lines 182-185:
     KT=$(kill_tree "$1"); bi=0
     while :; do a=0; for p in $KT; do [ -n "$(pstart "$p")" ] && a=1; done
       [ $a = 0 ] || [ $bi -ge 40 ] && break; sleep 0.25; bi=$((bi+1)); done
     [ $a = 1 ] && return 2; wait "$1" 2>/dev/null; return 1
     ```

3. **Known-open, confirmed: a manual rollback that ends not-verified doesn't escalate** (`gadget.sh:213-214`).
   - **Worse than stated:** `cleanup()` touches `/tmp/mk20-flapmon-stop` (117), so flapmon and the waiter stand down as soon as any rollback starts. Nothing is left to retry or escalate.
   - **Fix:** escalate by default and let supervised callers opt out: `[ "${MK20_RB_SUPERVISED:-0}" = 1 ] || { escalate "rollback not verified" rollback-NOT-verified; exit 3; }`. Set `MK20_RB_SUPERVISED=1` in the flapmon export (346), both waiter calls (355) and `rbn` (`mk20d:175`).

4. **Known-open, confirmed: `escalate()` can fail silently or hang** (`gadget.sh:191-194`).
   - **Same hang elsewhere:** `rb_status` (150) also writes to the FAT while holding fd 7.
   - **Nobody retries:** rc 3 tells flapmon (`flapmon.sh:57`), the waiter and mk20d not to write their own flags, so a failed `touch` means nobody escalates.
   - **Fix:** write the flags in a bounded child that doesn't hold fd 7, and return a real status. Fall back to the staged hardreset, as `loader.sh:182` does:
     ```sh
     escalate() { log "ESCALATE: $1"
       ( rb_status "$2"; touch "${MK20_FLAP_FLAG:-/mnt/SDCARD/mk20-no-composite}" && touch "${MK20_FLAP_REBOOT:-/mnt/SDCARD/cmd-reboot}" && sync ) </dev/null 7>&- & ep=$!
       bounded_wait $ep 20 && [ "$BW_RC" = 0 ] && return 0
       log "ESCALATE: flags not written -> direct hardreset"
       [ -f /tmp/mk20/rescue-hardreset.sh ] && ( sh /tmp/mk20/rescue-hardreset.sh gadget-escalate </dev/null >/dev/null 2>&1 7>&- & ); return 1; }
     ```
     This version overwrites `BW_RC`, so save it before line 241. Callers should re-touch both flags on rc 3; doing it twice is harmless.

**MINOR**

1. **Known-open, confirmed: rc-3 exits skip clearing `gadget.busy`** (`gadget.sh:209`, `:244`, `:250`).
   - **Impact:** small. The only reader is `busy_wait` (`mk20d:152-156`), which waits at most 150 s once per boot, and a reboot is already pending.
   - **New leak from #6:** rc 4 also covers a lock error (no other owner), and a successful setup never clears the marker (238). A marker flapmon created then stays, and flapmon later sees `mine=0` and never removes it.
   - **Fix:** `rm -f` the marker before each `exit 3`. Give flapmon its own marker (`gadget.busy.fm`, always removed by flapmon) and have `busy_wait` check `gadget.busy*`.
2. **flapmon keeps its fd-8 single-instance lock through the retry loop** (`flapmon.sh:21`, `:49-61`). A stale rollback waiting up to 3 × 900 s holds fd 8, so a newer setup's flapmon can't get the lock within 5 s. The newer setup then always dies at `gadget.sh:350`: safe, but a re-run never succeeds. **Fix:** `exec 8>&-` after "FLAPPING -> rollback" (line 47); the generation token now guards the rollback.
3. **`rbn` doesn't pin `MK20_GEN`** (`mk20d:175-179`). flapmon exports it, and it reaches everything the rollback starts (KD fallback, add-adb, adbd). If mk20d ever inherits it, rc 5 counts as a failure and leads to escalation against a healthy newer composite. **Fix:** set `MK20_GEN=` in `rbn` (or pass the boot generation and treat 5 as done), and `unset MK20_GEN` in `gadget.sh` after the check. Also, line 179 should use `$S/...` instead of `/mnt/SDCARD`, so MK20_ROOT tests don't write the real path.
4. **Stale docs.** `gadget.sh:142-144` still says 240 s / 300 s / "rc 1"; it is now 330 s / 900 s / rc 4. `flapmon.sh:9` still says the marker is cleared after every attempt.
