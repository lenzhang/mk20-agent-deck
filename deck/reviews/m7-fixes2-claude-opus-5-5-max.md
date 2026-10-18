**Verdict:** 1 BLOCKER, 3 MAJOR, 9 MINOR. The `--rollback-now` once-guard breaks two of your goals outright: success must be truthful, and rollback must never wedge. `tf-restore-boot.sh` no longer has any path that can put a partial image at LBA0; what's left there is about identity and usability.

Line numbers are for 8904b63. I also read the surrounding code (`cleanup`, `watcher.sh`'s job cap, `mk20d`'s `busy_wait`) to confirm the failure paths.

### BLOCKER

**`gadget.sh:139-145` (and `RECOVERY.md:66-70`): `rollback.once` treats "running" the same as "verified".**
- **False success:** any caller that finds the guard directory exits 0 without checking anything. That includes the case where the owner is still inside `cleanup` or later fails verification.
- **Permanent no-op:** if the owner dies or hangs, every later rollback does nothing and returns 0 until a reboot or a new composite. One way that happens: a queued `--rollback` hits the watcher's 300-s job cap (`kill_tree`, `watcher.sh:76-78`). `cleanup` alone can take about 230 s: `kd_stop` ~30 s, `kd_start` ~23 s, `readd_adb` ~155 s.
- **Other `mkdir` errors:** a `mkdir` that fails for another reason (for example a full tmpfs) is also read as "done".
- **Weak verify:**
  - It doesn't poll UDC/ACM, so KD's own `setusbconfig` reset can give a false "NOT verified".
  - The unanchored `pgrep -f KeyboardDevice` matches any command line containing that string, such as the soak's adb probe.
  - It never checks that the ECM/HID links, the shim mounts or the PTY symlink are gone.

Fix: drop the marker. Serialize on a lock and get idempotency from a predicate:

```sh
stock_ok() { [ -e $C/acm.usb0 ] && [ ! -e $C/ecm.usb0 ] && [ ! -e $C/hid.usb0 ] && [ -n "$(udc_now)" ] &&
  [ -c /dev/ttyGS0 ] && [ ! -L /dev/ttyGS0 ] && ! grep -qE ' /data/(setusbconfig|appLunch\.sh) ' /proc/mounts &&
  pgrep -f '^/data/KeyboardDevice' >/dev/null; }
if [ "${1:-}" = --rollback-now ]; then
  mkdir -p /tmp/mk20; exec 7>/tmp/mk20/rollback.lock; i=0
  while ! flock -n 7; do i=$((i+1)); [ $i -gt 300 ] && { log "rollback lock busy >300 s"; exit 1; }; sleep 1; done
  stock_ok && { log "stock already verified"; exit 0; }
  log "rollback requested"; cleanup 7>&-   # adbd/KD must not inherit the lock
  i=0; until stock_ok || [ $i -ge 30 ]; do sleep 1; i=$((i+1)); done
  stock_ok && { log "rollback verified"; exit 0; }; log "rollback NOT verified ..."; exit 1
fi
```

The kernel drops the lock if the owner dies. Also delete `:231`, correct the RECOVERY text, and add tests for a concurrent caller, a killed owner and an unverified result.

### MAJOR

**`flapmon.sh:44-46`: nothing retries or escalates after a failed rollback.**
- `cleanup` has already touched the stopfile (`gadget.sh:117`), so the confirm waiter exits.
- `mk20d` only rolls back during `boot`.
- A good (non-pending) release has no loader deadline.

So a half-undone gadget (UDC unbound, KD down) stays that way until someone power-cycles the board.

Fix:
1. Retry about 3 times, 20 s apart.
2. If it still fails, write a one-boot stock marker (e.g. `/data/mk20/state/stock-once`; `mk20d boot` reads it, uses MODE=stock and deletes it), then run `touch /mnt/SDCARD/cmd-reboot; sync`.

This depends on the polled verify above; otherwise false negatives would reboot a healthy device.

**`gadget.sh:155, 231, 236, 241-246`: a normal run can overlap a rollback that is still running.** A flapmon, waiter or mk20d rollback takes about 100–230 s, and a queued `--run` can start during it. Then:
- Both scripts reconfigure configfs and KD at once.
- `:231` removes the live guard.
- `cp -f` truncates `/tmp/mk20-gadget.sh` while a rollback that just started may still be parsing it. If it hits EOF there, it exits 0 silently.
- The old flapmon holds the fd-8 lock while it waits on its rollback, so the new flapmon gets `exit 3` after about 4.5 s. The new run then `die`s and starts a second `cleanup` alongside the first.

Fix:
- At preflight, take the same `rollback.lock`; if it's held, refuse with "ABORT rollback in progress".
- Hold the lock for the whole run, and add `7>&-` to the adbd, bridge-loop, flapmon and waiter spawns.
- Stage both script copies by copying to a temp file and then `mv -f`.

**`mk20_accept.py:249-260`, `mk20_soak.py:64, 77-80`: the bridge-client check can go stale before the connect.**
- One netstat sample is followed by three separate connects.
- In accept they can stretch over about 45 s (`get_info` runs `uv run` with a 40-s budget). In soak, a route lookup and two pings come first.
- A deck client that reconnects inside that window (for example after the Mac wakes) gets kicked.

Fix:
- Right before each connect, re-check `bridge_clients() == 0` on the device. On the Mac, `lsof -nP -iTCP@172.30.250.2:17890 -iTCP@169.254.7.2:17890` must return nothing; this also catches a connect still in progress.
- Skip all connects while the deck app is running.
- Drop the separate primary `tcp_bound`, since getInfo already proves that path.
- These steps only narrow the race. Closing it fully needs the bridge to refuse a second client instead of replacing the first.

### MINOR

- **`flapmon.sh:43-45`:**
  - **Problem:** flapmon creates `gadget.busy` before the rollback takes the guard. When the guard is already held, the rollback returns 0 at once and flapmon never removes the marker, while the owner's `cleanup` (`gadget.sh:134`) may already have deleted it. mk20d's `busy_wait` (`mk20d:152`) then waits for its full 150-s cap.
  - **Fix:** `rm -f "$BUSY"` after the command returns, in every case.
- **`gadget.sh:251-252`:**
  - **Problem:** `sleep 1; kill -0 $WP` can't tell a waiter that never started from one that exited because `mk20-composite-ok` already existed. An early confirmation therefore rolls back a confirmed composite and logs FAIL.
  - **Fix:** give the waiter a ready marker like flapmon's, or check that file before `die`.
- **`gadget.sh:113-145`:**
  - **Problem:** `/mnt/SDCARD/gadget-ecm-hid.status` still says `gadget-ok[-boot]` after any rollback, and RECOVERY §0 tells operators to read it.
  - **Fix:** write `rollback-verified`, `rollback-NOT-verified` or `failsafe` to it.
- **`mk20_accept.py:181-200, 261`:**
  - **Problem:**
    - `gadget.sh:59` records that adbd kills the adb shell's whole process group and that `trap HUP` doesn't help. That contradicts the "outlives the adb shell / never killed" comment at `:185-187`.
    - On HIDPENDING the writer dies, `hidtap.busy` is never removed, and every later probe reports HIDBUSY until reboot.
    - If the writer does survive, it holds `/dev/hidg0` open through a later teardown.
    - The probe also runs by default.
  - **Fix:**
    - Make the probe opt-in.
    - After the 5-s wait, run `kill $! 2>/dev/null; rmdir /tmp/mk20/hidtap.busy` in the same shell. This is safe: the blocked second write hasn't queued anything, and the first report is all keys released.
    - Store the writer's pid in the busy directory, and reclaim the directory when that pid is dead.
- **`mk20_accept.py:242`, `mk20_soak.py:70`:**
  - **Problem:** the primary ping isn't bound to an interface. Through a TUN it can get a reply faked by the tunnel.
  - **Fix:** use `ping -b <iface>` and skip it when there's no MK20 interface, as `soak:74` already does for the link-local ping.
- **`mk20_accept.py:148`:**
  - **Problem:** the "fresh boot" check compares uptimes, so it fails falsely if the device had been up only a few minutes before `--reboot`.
  - **Fix:** compare `/proc/sys/kernel/random/boot_id` before and after.
- **`tf-restore-boot.sh:40-53`:**
  - **Problem:** any GPT image of the same size with a self-written sidecar passes, and the target disk is never checked to be an MK20 card.
  - **Fix:**
    - Require image GPT entry 4 to be the boot partition: `read f l < <(dd if="$SRC" bs=1 skip=1440 count=16 2>/dev/null | od -An -tu8)`, then `(( (l-f+1)*512 == 6451200 ))`.
    - If the card still has a GPT, require its disk GUID (LBA1 + 56) to match the image's unless `--new-card` is given, and ask the user to type `diskN` to confirm.
    - For `--image`, refuse any sha that isn't one of the three listed in RECOVERY §5.
- **`tf-restore-boot.sh:76`:**
  - **Problem:** the readback goes through `dd bs=1 count=$BYTES`. For a whole card that's about 4 billion one-byte syscalls, i.e. hours, and a user who aborts may think the write failed.
  - **Fix:** replace that stage with `head -c "$BYTES"`.
- **`RECOVERY.md:46-47, 108`:**
  - **Problem:** with a dirty FAT, macOS may offer "Initialize…" when the card is inserted, and one click repartitions it.
  - **Fix:** add "click Ignore; never Initialize or Erase."

The current HEAD already differs for accept/soak: active TCP and HID writes are opt-in, and the primary ping is bound. So the unbound-ping item is fixed there, but the stale bridge-client check and the HID busy-dir leak still apply when those flags are used.
