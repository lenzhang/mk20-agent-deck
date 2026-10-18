## Release gate review: MK20 fw 1.1.4, round 8 (d2d1ece..36e0b01)

I found one release blocker. It's a false success exit code, and this diff introduced it. I didn't find any other path to a bricked board, a wedged fd-7 mutex or a stale rollback tearing down a live composite. The other r8 changes check out:
- the `kill_tree` snapshot and `any_alive` check
- the bounded `escalate()` writer with fd 7 closed
- `stuck()`
- the generation read-back and `gen_stale` rules
- flapmon's sticky busy-marker ownership
- the detached watcher jobs
- mk20d's `rbn` pinning `MK20_GEN=''`

### BLOCKER 1: the setup supervisor exits 0 after a failed setup

**Where:** `deck/firmware/release/share/gadget.sh:271-272`. The cause is the new `bounded_wait` call in `escalate()` at `:205`.

**Path:**
1. `setup_main` fails (`die`), its own failsafe runs, but stock still isn't verified after 30 s.
2. The supervisor calls `escalate`.
3. In r8, `escalate` runs `bounded_wait $! 20`, which overwrites the global `BW_RC` with the flag writer's exit code. That is 0 whenever both `touch` commands succeed.
4. `exit "$BW_RC"` therefore exits 0.

**Effect:** `gadget.sh --boot` reports success. mk20d gets `grc=0` and skips its probation check at `mk20d:162-164` ("FAIL composite setup rc=… during probation"). If the selftest passes before the watcher acts on `cmd-reboot`, the release gets confirmed. In r7 this exit returned the real setup exit code.

**Fix:** save the exit code before anything can overwrite it (or have `escalate` save and restore `BW_RC`):

```sh
  src=$BW_RC; [ "$src" = 0 ] && exit 0
  # setup failed and ran its own failsafe: verify stock, else escalate (flapmon is stopped at this point)
  i=0; while ! stock_ok && [ $i -lt 30 ]; do sleep 1; i=$((i+1)); done
  stock_ok || escalate "setup failed (rc $src) and its failsafe did not reach stock" setup-failsafe-NOT-verified
  rm -f /tmp/mk20/gadget.busy; exit "$src"
```

Add a test: setup returns 1, stock is never verified, and the flags are writable. Expect exit code 1 and both flag files present.

### NON-BLOCKING
1. At `gadget.sh:44`, the "no gadget.env → refused" message is only `echo`ed, and the detached job now sends output to `/dev/null`, so a refused queued `--run` leaves no trace. Use `log` there.
2. The watcher job now always finishes as "started" with exit code 0. Any host tool that read the job result as the setup/rollback outcome must poll `gadget-ecm-hid.status` instead.
3. In `escalate` (`:203-206`), the status file says STUCK/ESCALATED before the flags exist. A `sync` longer than 20 s, or the watcher deleting `cmd-reboot` before the `-e` check, logs "ESCALATE FAILED" (exit 1) although the reboot is already armed. It's harmless; check the files before `sync` and write the status last.
4. The worst-case setup-supervisor hold is about 900 s: setup + rollback timeouts + 30 s, two kill/check windows of up to 10 s, the 20 s escalate, and loop fork overhead. That equals the `RBTRIES` default, so waiters can get exit 4, which is retried. Raise the default (e.g. 4400) and fix the `:179` comment.
5. `:288` touches `flapmon-stop` before the generation read-back at `:290`. On a re-run, a failed publish stops the live composite's flap monitor and confirm waiter, then the supervisor escalates to a reboot. Publish and read back first, then touch.

VERDICT: NO-GO
