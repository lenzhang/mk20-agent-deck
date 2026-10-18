I have everything I need. Here's the review.

## MK20 M3 review — installer, rescue loader, host tools

**Verdict:** No blocker. Power-cut safety holds (atomic rename + pending-before-slot), tar validation is sound against the ustar attacks tested, and every M2 blocker/major is genuinely fixed. Items below are MAJOR/MINOR hardening.

### M2 findings — fixed
- Boot self-test rc no longer clobbered by `health` (decision on `$src`; `( health )` runs after in a subshell; vars `local`) — `mk20d:147-158`.
- Unknown kernel → `MODE=none`, zero USB mutations (`mk20d:128,143`); vendor/kernel gate failures fall to stock, never strand USB.
- `CFG_WARN` reset, internal-whitespace keys rejected, `eval` replaced by explicit `case`, exact tmpfs shim-mount match, bridge `SO_BINDTODEVICE`+wildcard refusal, fail-closed `gadget.env`, maint `flock` lifetime lock. All have tests.

### MAJOR
1. **`netstat` dependency still unfixed (`mk20d:44` `listening()`).** M2 flagged this. If the device BusyBox lacks the `netstat` applet, `listening()` always returns false → composite boot fails self-test *every* boot → perpetual rollback to stock even when the bridge is up (stock+adb survives, so not a brick, but composite never confirms). Parse `/proc/net/tcp` (hex `ECM_IP:PORT`) instead, or assert the applet exists.

### MINOR
2. **`switch` holds no lock (`loader.sh:176-186`); install uses `install.lock` (fd6), boot/uninstall use `loader.lock` (fd7).** In the normal watcher flow, boot precedes any install job so they don't overlap, and boot's "stale pending cleared" path fails safe. But an out-of-band `loader install` concurrent with `boot` can interleave the pending/slot writes. Give `switch` (and install's slot writes) `loader.lock`, or have `boot` also take `install.lock`.
3. **Boot never cleans `.incoming-*` (`loader.sh:121`).** Plan §11 requires it; only `prune` (install-time, `loader.sh:253`) does. A crashed install leaves `.incoming-<v>` on /data until the next install. Harmless on ext4, but a plan deviation and the power-cut test already tolerates it — add a sweep in `boot`.
4. **Tarball re-read after the pin (`loader.sh:206` sha vs `211/220/230` list/MANIFEST/extract).** The pinned sha is checked once, then `$tb` is read three more times. A swap in `/data/mk20/incoming` between check and extract defeats the pin; verify_dir then trusts the swapped MANIFEST. All-root/trusted-host keeps this low, but since tar safety is the headline concern, copy `$tb` to a private tmp, verify the sha there, and list/extract from that copy.
5. **`tar -tv` parsing coupled to a 6-column layout (`loader.sh:215-216`, `NF!=6`).** Correct for BusyBox 1.27.2 (tested) and it catches symlink/hardlink/device/space-name entries via the mode char + field count. But a BusyBox built without owner/group in `-tv` makes *every* entry fail → all installs refused. Fine while the device BusyBox matches the test one; add a comment pinning that assumption.
6. **Blocking PTY write (`ttygs_tcp_bridge.c:24-27` `wall`, used `:93`).** Still blocking (predates M2); a wedged KD reader stalls all accepts. Open the master `O_NONBLOCK` with bounded drop, like `wsock`.
7. **Unbounded growth:** `uninstalled-<ts>/` dirs (`loader.sh:256`) and leftover `.incoming-*` accumulate on /data; add cleanup.

### Test gaps
- No PAX / GNU `@LongLink` / long-name or device-node tarballs (prompt calls these out). BusyBox shares its header parser between `-tv` and `-x`, so list≡extract, but a PAX `path=` test would lock that in.
- Power-cut matrix only crashes on `mv`/`sync`; none during `tar -xf`/`rm -rf` (benign, but unverified).
- No concurrency test (install vs boot; standalone prune vs boot).
- **Host tools have only shellcheck.** `mk20-install.sh` rc-parsing / `--wait` / reboot-via-watcher and `mk20-rescue-update.sh`'s two-home atomicity (torn FAT-only `lunch.sh`, `:19`) are untested.
- `netstat`-absent self-test behavior untested.

Net: the state machine and power-cut story are solid and the board never runs a half or unverified release; the one that can bite in the field is the `netstat` assumption.

Switched to Claude Opus 4.8

Claude Opus 5.5 hit a safety filter, and the conversation was automatically switched to Claude Opus 4.8. Start a new conversation to continue with Claude Opus 5.5, or continue this conversation with Claude Opus 4.8.


done rc=0
