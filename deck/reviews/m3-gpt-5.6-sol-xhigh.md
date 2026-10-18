Connection lost, reconnecting to https://agentn.global.api5.cursor.sh (attempt 1)...
Retry attempt 1...
# Verdict: not ready to merge

## BLOCKER

- **Archive TOCTOU enables validation bypass** — `loader.sh:200-230`. The tarball is hashed, listed, read for `MANIFEST`, then extracted through separate opens. A concurrent second `adb push` to the same basename can replace it after hashing/listing; extraction may then process unvalidated traversal, links, or device entries. Copy under the install lock to a private mode-600 staging file, verify that copy, and use only it; rehash before committing.

- **Host filename causes remote-shell injection** — `tools/m# Verdict: not ready to merge

## BLOCKER

- **Tarball TOCTOU bypass** — `deck/firmware/rescue/loader.sh:200-230`. Hashing, listing, reading `MANIFEST`, and extraction reopen the pathname independently. A concurrent overwrite/`adb push` after listing can extract unvalidated traversal, link, or device entries. Under the install lock, copy to a private mode-600 staging file, hash that copy, and perform every operation on it; rehash before commit.

- **Remote-shell injection through filename** — `deck/tools/mk20-install.sh:15-27`. The shell glob is not a semver validator: its `*` accepts spaces and `;`. `$base` is then interpolated unquoted into an `adb shell` program. Validate `V` with the same strict regex as the loader and pass arguments using robust shell quoting or upload under a fixed remote filename.

- **Vendor mismatch still mutates USB** — `deck/firmware/release/sbin/mk20d:126-143`, `share/mk20-add-adb.sh:20-35`. Unknown kernels now fail closed, but vendor mismatch selects stock and invokes a helper that kills adbd and unbinds UDC. This M2 blocker remains. Treat any vendor-check failure as `MODE=none`; test that UDC/functions/processes remain unchanged.

## MAJOR

- **Archive validation is format-fragile and incomplete** — `loader.sh:210-234`. Security depends on verbose output having exactly six whitespace fields, with `$NF` as the name. There are no path/component/listing-size limits. The manifest duplicate check at lines 88-89 can be bypassed with repeated whitespace; a duplicate listed path plus one unlisted regular file defeats the later count-only check. Parse a canonical machine-readable archive format or constrain the accepted build format, enforce length/size limits, reject duplicate archive members, and compare the exact sorted manifest path set against extracted files.

- **Interrupted extraction can wedge future installs** — `loader.sh:208-209,229-230,253`. Space is checked before stale `.incoming-*` is removed. A power cut or ENOSPC can leave a large incoming tree; retries fail the space check forever until manual `prune`. Remove only the target’s stale incoming directory before checking, reserve explicit working space, and validate aggregate uncompressed member size. Boot should also clean stale incoming trees as §11 requires.

- **State locking is not coherent** — `loader.sh:120,169-198,245-258`. Only boot/uninstall use `loader.lock`; install/prune use another lock; switch, rollback, and confirm use neither. Install can race uninstall, and `MK20_INSTALL_LOCKED=1` publicly bypasses prune locking. Use one lifetime state lock for every pointer/state mutation, with a fixed lock order for extraction and state operations; represent internal lock inheritance by an already-open FD, not an environment flag.

- **Uninstall does not guarantee stock behavior** — `loader.sh:255-259`, `rescue/watcher.sh:110-118`. It only archives slots/state. On reboot, legacy `boot-every` jobs may re-enable the old composite, and a concurrent install may recreate slots. Archive/disable legacy jobs atomically, exclude installs, roll the active gadget back to stock, then request only `hardreset.sh`.

- **Busy marker has a race** — `gadget.sh:110-131`, `mk20d:145-148`. The marker appears only after cleanup starts; rollback can begin just after mk20d’s pre-sampling check, causing three false failures. Create it in flapmon before launching rollback and check it before and after every sample.

- **`gadget.env` is not an atomic trusted contract** — `mk20d:129-138`, `gadget.sh:34-46`. It is written directly and gadget accepts its values without revalidation. Write mode-600 temp + rename, validate every parsed field, and require the file for queued release runs.

## MINOR / test gaps

- `mk20-rescue-update.sh:19-25` updates files individually, not as a compatible rescue bundle; add generation directories plus an atomic pointer.
- Tests default to any host BusyBox (`conftest.py:8-21`), so 1.27.2 portability is optional.
- Missing tests: GNU/BusyBox listing differences, long/PAX/GNU names, devices/FIFOs, duplicate members, TOCTOU replacement, mid-extract ENOSPC, install/uninstall/boot races, host-tool injection/ADB targeting, real gadget environment validation, and power cuts beyond only `mv`/`sync`.
- The M2 health-result, config, unknown-kernel, listener-binding, maint-lock, and JSON findings are substantially fixed; blocking PTY writes and master `POLLERR/POLLNVAL` remain in `ttygs_tcp_bridge.c:24-27,84-96`.
done rc=0
