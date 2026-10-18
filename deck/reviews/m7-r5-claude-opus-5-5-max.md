**Verdict:** Both r4 BLOCKERs are fixed. Setup and rollback can't overlap any more, and nothing signals a process it didn't spawn. I found no new BLOCKER. The "bounded" claim still has three gaps (MAJOR 1–3), plus six MINORs.

**Checked and fine**
- **No fd 7 leak into a long-lived process.** Every daemon spawn closes it: lines 93, 97, 107, 110, 130, 223, 242, 274, 281. The KD started by procd never inherits it.
- **No holder waits on anything that needs fd 7.** There is one lock-order inversion: flapmon holds fd 8 and waits for fd 7, while a re-run holds fd 7 and waits for the new flapmon's fd 8. It can't deadlock, because the new flapmon gives up after about 5 s, so the re-run fails and ends in stock.
- **PID reuse is handled.** The start-time compare is needed, because ash reaps background jobs during any foreground wait.
- **Lock failure in setup is safe.** It aborts before `trap cleanup EXIT`, so nothing is changed.

**MAJOR**

1. **`gadget.sh:166-169`: `wait $CP` after `kill_tree` has no time limit.**
   - The cleanup subshell does the UDC write itself (line 119). If that write sticks in D state, SIGKILL stays pending and `wait` never returns.
   - flapmon (`flapmon.sh:50`) then never retries or escalates, which contradicts `flapmon.sh:12-13`.
   - The stuck child still holds the inherited fd 7, so every later caller is refused. The device wedges with no reboot.

   Fix: put a limit on the reap, and escalate from inside the rollback, because no other transition can ever run:
```sh
if [ -n "$CST" ] && [ "$(pstart $CP)" = "$CST" ]; then
  log "rollback cleanup exceeded ${RBT}s -> killing own child tree $CP"; kill_tree $CP; i=0
  while [ "$(pstart $CP)" = "$CST" ] && [ $i -lt 40 ]; do sleep 0.25; i=$((i+1)); done
  if [ "$(pstart $CP)" = "$CST" ]; then
    log "cleanup $CP unkillable (D state)"; rb_status rollback-STUCK
    touch /mnt/SDCARD/mk20-no-composite && touch /mnt/SDCARD/cmd-reboot; sync; exit 3
  fi
fi
wait $CP 2>/dev/null
```
   In flapmon, after the rollback returns: `[ "$rc" = 3 ] && exit 1`, with no further retries.

2. **`gadget.sh:184-188`: setup holds fd 7 through its body and its EXIT-trap `cleanup`, but neither is supervised, verified or escalated.**
   - At that point flapmon either hasn't started or was just stopped by line 117, so nothing escalates.
   - If setup hangs (UDC writes at 197, 228 or 119, `$KDSVC`, add-adb), every rollback is refused after 300 s and the device stays half-built.
   - r4 at least let a concurrent rollback try; r5 trades that overlap for a wedge.

   Fix:
   - Move lines 162-173 into `rb_supervised()`: child with a time limit, limited reap, `stock_ok` wait, return 0/1/3.
   - Use it for `--rollback-now` and in the EXIT trap: `trap '[ "$OK" = 1 ] || rb_supervised || { touch /mnt/SDCARD/mk20-no-composite && touch /mnt/SDCARD/cmd-reboot; sync; }' EXIT`.
   - Ideally run the setup body as a supervised child too.

3. **`gadget.sh:281` (pre-existing; the new timeout kill makes it easier to hit): the confirm waiter ignores the rollback's exit code.**
   - Its cleanup has already stopped flapmon (line 117).
   - So an unverified or timed-out rollback leaves the device broken, with no retry and no reboot.
   - mk20d has the same gap unless it acts on a non-zero exit code.

   Fix (in the waiter string): `… --rollback-now || { sleep 20; sh /tmp/mk20-gadget.sh --rollback-now; } || { touch /mnt/SDCARD/mk20-no-composite && touch /mnt/SDCARD/cmd-reboot; sync; }`.

**MINOR**

4. **`gadget.sh:158` (with line 97): `kill_tree` kills everything still parented under the cleanup subshell.**
   - That includes the fallback KD: `( exec /data/appLunch.sh ) &` stays its child through the roughly 95-s `readd_adb` tail, which is when the timeout fires.
   - It also includes adbd, if `mk20-add-adb.sh` runs it in the background.

   Fix: double-fork daemons started inside cleanup, e.g. `( ( trap '' HUP INT; exec /data/appLunch.sh … 7>&- ) & )`, and do the same for adbd in add-adb.

5. **`gadget.sh:164-165` vs `160`: the time limits are loop counts with forks in every pass, and they aren't derived from each other.**
   - cleanup's own bounded waits already add up to about 218 s (kd_stop 30, umount 10, kd_start 23, readd_adb 60+5+90). That's before up to 7 add-adb runs and the `$KDSVC`/setusbconfig calls.
   - So the 240-s limit can kill a healthy rollback halfway through add-adb, with the UDC unbound. It then reports "NOT verified" and a second full rollback follows.
   - Waiters have only about 25 s of margin over the owner's worst hold time.

   Fix: use deadlines from `/proc/uptime`, set RB_TIMEOUT ≥ 330, and set the lock wait ≥ RB_TIMEOUT + 90.

6. **`gadget.sh:115/134` and `flapmon.sh:50`: `gadget.busy` has no owner.**
   - It's left behind when the supervisor kills cleanup, and the waiter and mk20d paths never clear it.
   - flapmon deletes it after a *refused* attempt, while another owner is still mid-transition.

   Fix: the rollback runs `rm -f /tmp/mk20/gadget.busy` after a successful reap, and flapmon leaves the file alone when its attempt was refused.

7. **`gadget.sh:281/274` vs `160-161`: a stale request can tear down a newer composite.**
   - The waiter's last check can be up to 2 s before it calls the rollback.
   - If a re-run takes the mutex in that window, the rollback waits, then removes the new (possibly confirmed) composite.

   Fix:
   - After `tl_lock`, setup writes `$$ $(pstart $$)` to `/tmp/mk20/gen`.
   - flapmon and the waiter call `--rollback-now <gen>`.
   - On a mismatch the rollback exits with a distinct "superseded" code.
   - Calls with no token still run unconditionally.

8. **`gadget.sh:154-155`, `164`: the lock helper isn't robust.**
   - **`exec` redirect failure exits the shell.** In ash, a redirect error on the special builtin `exec` exits immediately (rc 2), so the "refused" log line never runs. Fix: add `touch "$TL" 2>/dev/null || return 1` before it.
   - **The env knobs aren't validated**, and they reach the rollback through flapmon's environment:
     - A non-numeric `MK20_*_LOCK_TRIES` makes `[ "$n" -ge x ]` fail forever, so the lock wait never ends.
     - A non-numeric `MK20_RB_TIMEOUT` either kills the rollback after it has already started the cleanup child, or evaluates to 0 and kills a healthy cleanup immediately.

     Fix: `case $v in ''|*[!0-9]*) v=<default>;; esac` for each knob.

9. **`test_rollback_guard.py:112`: the fd 7 test has gaps.**
   - Its regex misses the `( … ) & }` form at line 97.
   - It doesn't check the daemon-starting calls that don't use `&` (lines 93, 107, 110, 130).
   - Nothing tests setup-vs-rollback exclusion, or a daemon inside the killed tree.
   - Nit: `gadget.sh:78` (`$KDSVC stop`) has no `7>&-`.

`tf-restore-boot.sh` and the accept/soak host tools aren't in this diff, so I have no findings for them.
