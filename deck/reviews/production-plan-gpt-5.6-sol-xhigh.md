# Decision: GO-with-conditions

The architecture is viable, but the updater and automatic rollback are not production-safe yet. Do not enable M4 installs until the following blockers are closed.

## Blocking issues

1. **FAT crash consistency**
   - “Rename is atomic enough” is not a sufficient guarantee for WDT resets or power loss. FAT has no journal, and shell-level `fsync` does not necessarily flush the directory entry or SD-card cache.
   - The install sequence never promotes `.incoming-<v>` to `releases/<v>` before switching `current`.
   - Use two pointer slots containing generation, version, MANIFEST digest, and checksum. Write/flush the inactive slot, then select the highest valid generation at boot. Strictly reject absolute paths, `..`, malformed versions, and oversized pointers.
   - Fault-inject resets after every write/rename. Clean stale `.incoming-*` on boot.
   - FAT does not preserve Unix modes. Define how binaries/modules are atomically deployed under versioned `/data/mk20/releases/<v>` paths; the current scripts expect `/data`, not FAT.

2. **Boot-counter rollback**
   - Specify a durable state machine: `pending → boot-attempt incremented → self-test → confirmed` or `rolled-back`.
   - Increment and flush the attempt counter **before** starting the pending release. Otherwise early hangs never count.
   - The internal 30-second health check can incorrectly confirm a release that broke host TCP access. Require host confirmation when available, or a stronger probation test including an actual KD request through TCP.
   - Rollback must validate `previous`, switch durably, then reset. If both releases fail, stop launching enhancements without entering a reset loop.

3. **Kernel coupling**
   - Compatibility must use an exact full-p4 SHA allow-list, not kernel version, vermagic, or “min/max SHA.”
   - Gate the ECM module, gadget endpoint allocation, KD/appLunch/setusbconfig hashes, and patched kernel as one compatibility set.
   - MTU 478 is a memory-safety requirement for this kernel’s broken DMA path. It must be configured before `usb0 up`, reported by health, and tested with over-MTU traffic.
   - Move kernel inventory and recovery ahead of userland installation. The external TF recovery path must be rehearsed from the actual stock image; consider backing up `/data`/p6 as well.

4. **Watchdog/reset interactions**
   - `hardreset.sh` is unsafe when procd’s watchdog is not running: `sysrq b` reaches the same hanging PSCI reset path, now with no armed watchdog. Production code must prove the hardware watchdog is armed—directly arm it if supported—or refuse remote reset and request physical recovery.
   - Flush rollback pointers, counters, and logs before stopping watchdog pings.
   - The watcher’s 300-second timeout kills only the immediate shell, not its descendants. It can interrupt `composite.sh` while gadget/KD cleanup continues independently. Add bounded operations and verifiable whole-job cancellation; test timeout during every gadget transition.

5. **KD vendor-behaviour shims**
   - The `dmesg -c` shim still consumes all kernel messages and filters a broad text match. Hash-gate known vendor binaries/scripts and fail closed on unknown versions.
   - Acceptance must intentionally trigger HID `SET_IDLE`/`SET_REPORT`, prove no `usbc0` reload, then roll back and verify both bind mounts are gone, stock `setusbconfig` works, and exactly one KeyboardDevice remains.

6. **Integrity and recovery**
   - A MANIFEST containing its own file hashes detects corruption only if its digest is trusted separately. Pin a host-supplied digest or sign releases.
   - Test missing/corrupt stub, pointers, both releases, full disk, read-only FAT, and invalid config. “Stock behaviour” must remain reachable without repeated resets.

## ECM subnet

Choose **link-local-only by default**: device `169.254.7.2`, host `169.254.7.1`, no router, with interface-scoped routing and duplicate-address detection.

No RFC1918 subnet is reliably collision-free with corporate VPNs, Docker/Kubernetes, or TUN products. `172.30.250.0/24` is the better configurable fallback; `172.31.250.0/24` should be rejected because of AWS VPC prevalence. Remove the `10.20.0.0/24` migration alias after one release because it reintroduces VPN collision risk.

Test routing concurrently with Docker, Tailscale, WireGuard, mihomo/Clash, and a corporate `172.16/12` VPN, asserting traffic uses the USB interface.

## Missing acceptance tests

- Power-cut/WDT fault injection at every installer, pointer, and counter operation; then `fsck`, remount, and boot.
- Ten consecutive WDT boots, including resets before self-test, during rollback, and with watchdog unavailable.
- Exact-kernel mismatch and module mismatch must make no USB changes.
- Low-memory/full-FAT install, malicious tar paths, interrupted upload, downgrade, and pruning while the old release runs.
- Host disconnect/sleep must not cause false confirmation or a rollback loop.
- Resource soak: 24 hours of TCP, ADB, HID, repeated KD restarts, log rotation, and flap monitoring.

## Milestone order

1. Baseline hashes, complete backup, recovery drill, WDT safety.
2. Test harness/CI and compatibility gates.
3. Release layout plus crash-safe pointer/state format and minimal stub.
4. `mk20d`, health, config, logging, and vendor shims.
5. Installer and rollback with fault injection.
6. Routing matrix and hardware soak.
7. Documentation and final two-run acceptance.

M1 currently depends on `mk20d`, which is not delivered until M3; those milestones must be reordered.