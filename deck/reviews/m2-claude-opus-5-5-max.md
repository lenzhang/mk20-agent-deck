**Not ready to merge.** The worst problem is a variable clash: `health` overwrites the boot self-test result, so one unretried sample decides whether the release is confirmed. I didn't modify anything or run any commands.

What holds up:
- **Config parser:** it can't be injected. Keys pass a regex and a `case` whitelist, values are validated first, and `eval "$1=\$2"` never re-expands them.
- **No reset loop:** `mk20d` never resets, and the loader deadline fires once per pending version.
- **Gates:** the kernel and vendor gates fall back to stock.
- **Maint and logs:** maint closes fds 7–9 and writes nothing to the FAT. Log rotation has one writer per file today.
- **Bridge:** the poll indexing is correct, including `ci = -1` after a client is replaced.
- **BusyBox 1.27:** the only problem I found is `df` line wrapping.

### BLOCKER

**`health` overwrites boot's self-test result.** `health()` sets the global `rc` (`mk20d:57`) and runs in the boot shell (`:108`). So the confirm decision at `:109` uses a fourth, unretried sample, not the three-sample result logged at `:107`.
- A release that failed all three samples is confirmed `good` if the fourth passes.
- A passing release is rolled back if the fourth sample hits a transient.
- Health also runs before confirm, so a hang in `df` or `ubus` blocks confirmation.

**Fix:** confirm first, then run `( health ) > …/selftest-last.json`. Make health's variables `local`.

### MAJOR

1. **The three samples span only 20 s (`mk20d:106`), but a flap rollback takes longer.**
   - `cleanup` unbinds the UDC, stops KD (up to ~30 s) and waits up to 20 s for it to come back. `readd_adb` can then unbind again over the next ~90 s.
   - A host-caused flap just before sampling can fail all three samples (`kd=0`, `udc unbound`). That costs a reset plus a rollback, although §11 calls this degraded.
   - **Fix:** `gadget.sh` holds a `/tmp/mk20/gadget.busy` marker through rollback and re-adding adb. While it exists, keep sampling (cap ~150 s, under the launch+480 s deadline), then require one PASS.

2. **The bridge isn't actually restricted to usb0.**
   - Binding to an IP doesn't bind to an interface. With the default `arp_ignore=0`, the box answers ARP for 169.254.7.2 on any interface, so a Wi-Fi neighbour can connect. That is the exact threat §11 describes.
   - `ttygs_tcp_bridge.c:48` still defaults to `0.0.0.0`.
   - **Fix:** set `SO_BINDTODEVICE "usb0"` on each listener and exit 2 if it fails. Make `--listen` mandatory and reject `INADDR_ANY`.

3. **Only `mk20d boot` applies the config and gates.** The queued `--run` (`gadget.sh:51`) forwards only `MK20_REL` and the log path. A manual re-enable therefore:
   - comes up on 10.20.0.2, bound to `0.0.0.0` (`:33`);
   - ignores `FEATURE_HID`;
   - bind-mounts the shims (`:187`, `:196`) without the vendor-hash gate, although §11 requires failing closed.

   **Fix:** move `vendor_check` and `kernel_has` into `mk20lib.sh`, and have `gadget.sh` run `cfg_load` plus the gates itself in release mode.

4. **`FEATURE_ADB=0` also removes adb from the stock fallback (`mk20d:93,103`).** The self-test ignores adb, so the box is confirmed with only ACM+KD.
   - **Fix:** the config setting applies only to the composite. The stock path always adds adb unless the FAT flag file exists, and health reports missing adb as degraded.

5. **`CFG_WARN` is never reset (`mk20lib.sh:27`).** Maint calls `health`, and so `cfg_load`, every 300 s, and the warnings pile up in RAM and in `health.json` on tmpfs.
   - The plan's own §3 sample config puts several keys on one line. That gives three warnings, silently ignores 8 of its 12 keys, and grows about 20 KB/day.
   - A 4 KB config of bad lines grows about 6 MB/day.
   - **Fix:** set `CFG_WARN=""` inside `cfg_load`.

### MINOR

- **`vendor_check` (`mk20d:24-31`):** it prints `ok` if `vendor.sha256` is missing or empty, and skips a last line with no trailing newline. Require 4 checked entries and add `|| [ -n "$h" ]`.
- **`kernel_line` (`:22`):** with an empty p4 sha it runs `grep "^ "`. Go stock unless the sha is 64 hex characters.
- **JSON (`:50-76`):**
  - `jstr` passes control and non-ASCII bytes through from `ALLOWLIST` and the state files. Use `tr -cd ' -~'`.
  - An empty `$mounts` produces `"shim_mounts":,`. Default it to 0.
  - Maint and host calls share the same temp file. Use `.tmp.$$`.
  - Use `df -Pk` to stop BusyBox wrapping long device names.
- **Maint (`:113-120`):** there's no lock, and the pid is written only after `cfg_load`, so a second loop can rotate logs twice. Add `exec 6>…/maint.lock; flock -n 6 || exit 0`.
- **Missing self-test checks:**
  - FAIL when shim mounts remain while composite is down (a stale no-op `setusbconfig`).
  - Degraded when the FAT has under 1 MiB free (§11) or the link-local alias is missing.
  - Health has no overall ok/degraded/fail status.
- **`netstat` (`:34`):** this is a new BusyBox applet dependency on a FAIL path. Check that the box's BusyBox includes it, or parse `/proc/net/tcp` instead.
- **`kd_count` (`:32`):** `pgrep -f` also counts a concurrent `sha256sum /data/KeyboardDevice`. Use `pgrep -x KeyboardDevice`.
- **Config file (`mk20lib.sh:66`):** `[ -f ]` follows symlinks, and a symlink to `/proc/kmsg` makes `wc -c` block forever. Reject symlinks, and validate `MK20_DEFAULT_SUBNET` (`:63`).
- **Bridge C:**
  - `POLLERR` or `POLLNVAL` on the master or a listener makes the loop spin.
  - The results of `ptsname` and `open(keep)` aren't checked (`:54-55`).
  - A flood of connects can fill `bridge.log` faster than the 5-minute trim.
  - `wall()` blocks when the PTY is full (this predates M2).

### Test gaps

- Boot where the three samples and the `health` sample disagree (this would catch the BLOCKER).
- Self-test while a rollback is in progress.
- Repeated `cfg_load` calls (would catch the warning growth).
- The real `gadget.sh` with the config settings applied; `test_mk20d.py:15` replaces it with a recorder.
- The bridge binary: multiple `--listen` addresses, bad arguments returning 2 with no symlink left behind, and client replacement.
- The maint spawn path, which the fixture disables with `MK20_MAINT=0` (`:28`): check its open fds and a duplicate start.
- Control bytes in JSON fields, and a missing `vendor.sha256`.
done rc=0
