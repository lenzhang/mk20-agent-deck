No direct config command injection was found: the file is never sourced, and `eval` is currently reached only after key/value validation. However:

## BLOCKER

- `deck/firmware/release/sbin/mk20d:89-103` — Unknown kernels and vendor-hash failures select “stock” but still run `mk20-add-adb.sh`. That helper kills adbd and unbinds the UDC (`share/mk20-add-adb.sh:20-35`); on an unsupported/tampered system, failure can leave no ACM, KD transport, or adb. A confirmed “good” release has no deadline rollback, so this can persist across boots. Fix: unknown kernel or vendor mismatch must perform zero USB mutations. Only add adb when the exact kernel capability and required vendor hashes are known-good; test that UDC/functions remain unchanged on every gate failure.

- `deck/firmware/release/sbin/mk20d:105-112`, `:52-57` — `health()` assigns global `rc`, overwriting the three-sample self-test result before confirmation. A fourth, differing sample can incorrectly confirm or reject a pending release. Fix: save `boot_selftest_rc` before calling health and use only that for confirm/rollback; make health variables local.

## MAJOR

- `deck/firmware/release/share/mk20lib.sh:27,60-78` — `cfg_load` never clears `CFG_WARN`; maint appends identical warnings every five minutes forever. Also, deleting all key whitespace means malformed `FEATURE_ ADB=0` becomes valid and can disable adb. Fix: reset warnings at entry, trim only leading/trailing whitespace, reject internal whitespace, and replace both `eval`s with explicit case assignments. Validate environment-provided defaults through the same validators.

- `deck/firmware/release/sbin/mk20d:25-29` — Any mount-table occurrence of a vendor pathname bypasses its hash, without checking that it is an expected MK20 shim mounted at that target. Fix: parse the mountpoint field exactly and verify the expected tmpfs source plus shim digest, or verify the underlying vendor copy before accepting the mount.

- `deck/firmware/release/share/gadget.sh:174-178`; `sbin/mk20d:33-47` — Link-local setup failure is merely logged, and self-test accepts any listener using that port. The release can therefore confirm without its required rescue address, or against an unrelated/wildcard listener. Fix: make alias failure a host-independent self-test failure and verify listeners on both exact ECM and link-local addresses, owned by the bridge.

- `deck/m2-scripts/bridge/ttygs_tcp_bridge.c:48,52-61,72-84` — No `--listen` silently exposes `0.0.0.0`; PTY slave/open/termios failures are unchecked; a client can fill the blocking PTY write and hang all accepts; `POLLNVAL/POLLERR` and `wall()` errors are ignored. Parsing also accepts whitespace/`+` despite claiming strictness. Fix: require explicit listeners, scan decimal digits, check every syscall, use nonblocking PTY writes with bounded buffering/disconnect, and handle all poll errors.

- `deck/firmware/release/sbin/mk20d:113-124`; `share/gadget.sh:183-224` — PID-file checks are racy and no lifetime lock protects maint, bridge supervisor, or flapmon. Repeated boot launches can create duplicates, stale port owners, and continuous tmpfs logging. Fix: each long-lived loop should hold its own nonblocking `flock` descriptor for its lifetime and close unrelated inherited descriptors.

- `deck/firmware/release/share/mk20lib.sh:12-24`; `sbin/mk20d:95-100,120-123` — Rotation races with concurrent writers; gadget’s long-lived redirection can continue writing to renamed `.1`, defeating caps. Bridge trimming rewrites a file while it is open for append. Fix: serialize rotation/writes per file, avoid process-lifetime log descriptors, and rotate bridge logs by rename/reopen.

- `deck/firmware/release/sbin/mk20d:50-77` — `jstr` deletes quotes/backslashes rather than JSON-escaping and leaves other control bytes; failed numeric probes can emit empty/invalid JSON. Failed writes may return stale health. Fix: use a real byte-wise JSON escaper, validate/default every numeric field, and fail rather than serving stale output.

## MINOR / tests

- `sbin/mk20d:108` stores only `selftest-last.json`, not the required per-boot record.
- `deck/tests/test_mk20d.py` lacks bridge C tests, transient self-test/health disagreement, exact-listener/link-local failure, duplicate daemon launches, concurrent rotation, control-byte JSON, and gate immutability. The vendor-mismatch test currently codifies the unsafe add-adb behavior.

The loader’s attempt counter and per-version reset marker appear to prevent an automatic reset loop, but the gate bug can still strand USB access.
done rc=0
