# Review verdict: NOT GO

## BLOCKER

- **Failed first install is later booted as trusted.** `loader.sh:92-96,107-109`: `rollback_to_good` clears `pending` before discovering there is no `good`. Sequence: attempt 1 fails → boot 2 returns stock → boot 3 launches the same failed release without probation. `switch` also exposes the new slot before writing `pending` (`loader.sh:148-149`); a crash between those writes has the same effect. Use one durable transaction record written before slot activation, reconcile it at boot, and never clear/quarantine a failed slot until a valid `good` pointer is committed.

- **Checksum failures can still execute unverified root scripts.** `lunch.sh:21-27` deliberately runs the checksum-failed FAT watcher. `watcher.sh:22-35,57-64,131-137` hashes a source, then copies/moves and executes it without validating the staged bytes against one immutable expected digest; `rescue_copy` even rereads the mutable checksum after copying. A write race or partial cross-filesystem `mv` can run arbitrary content and remove watcher/ADB. Capture and validate one 64-hex digest, copy to a new tmpfs file, hash that copy, then atomically rename and execute it; remove the unverified fallback.

- **Unbounded FAT logging can consume the recovery channel.** `watcher.sh:18-20,59-70`, `loader.sh:17`, `mk20d:12-15`, and `hardreset.sh:18-20` append to the nearly-full 8 MiB FAT. Only `watcher.log` receives limited startup truncation. Once full, command/job files cannot be created, potentially leaving neither ADB nor usable watcher commands. Put logs on `/data` or enforce the §11 aggregate 256 KiB cap and 1 MiB reserve before every FAT write.

## MAJOR

- **“No runnable release” launches legacy mutations instead of stock recovery.** `watcher.sh:91-100` runs every `boot-every` job for all nonzero loader results, including rc 11 (both releases bad). Handle rc 10/12 as legacy only; rc 11 must retain stock ACM+KD+ADB plus watcher.

- **MANIFEST verification is not fail-closed.** `loader.sh:67-69` accepts `kernels=any`, violating the exact kernel allow-list. `mk20-release-build.sh:11,20` permits multiline `VERSION`/build IDs; a build ID can inject an earlier `kernels=any`. `loader.sh:70-87` follows symlinks/special files, ignores cleanup/copy failures, allows duplicate paths, and does not prove `sbin/mk20d` was a listed regular file. Remove `any`; validate single-line metadata; reject symlinks/non-regular files and duplicate/control-character paths; stage into a fresh directory with every operation checked.

- **Automatic reset bypasses verified rescue.** `loader.sh:133-135` executes the mutable FAT `hardreset.sh`, not the verified rescue copy. Have the watcher stage a verified tmpfs hardreset and pass that fixed path to the loader.

- **Timeout does not kill the whole job.** `watcher.sh:28,63-66` sends only TERM and immediately kills the timeout helper; TERM-ignoring descendants survive. Freeze and enumerate descendants, TERM with a bounded grace period, then KILL/reap and rescan survivors.

- **Required failure tests are absent.** `test_loader.py:61-64` omits the revealing third boot. There is no power-cut/write-step matrix, concurrent confirm/rollback/deadline test, symlink/FIFO/TOCTOU or `kernels=any` test, full-FAT test, rc-11 watcher test, or TERM-ignoring descendant test. `test_rescue.py:44-49` instead codifies the unsafe unverified fallback.

## MINOR

- `mk20-release-build.sh:12-27` requires GNU `stat`/`tar`; Tina BusyBox and default macOS tools do not support these options. Either declare a GNU host-only build environment or provide portable tooling.