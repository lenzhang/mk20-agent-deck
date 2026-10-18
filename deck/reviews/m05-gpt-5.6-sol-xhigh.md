## BLOCKER

- `remote/postswap-check.sh:15`: failed `flock -n 8` is ignored and p4 is written anyway. If bootswap or hardreset owns the lock, this permits concurrent writes or a WDT reset during `dd`. Never bypass the lock: poll with a deadline, then abort without modifying p4. Acquire it before removing the boot-once recovery file and revalidate inputs afterward.

- `remote/postswap-check.sh:16-19`: the pipeline reports `tail`’s status, hiding `dd` failure; even a bad readback still proceeds to hardreset. This can reboot into partial p4. Log without a pipeline, require successful `dd`, expected image size, and `R=$PS`; otherwise retain recovery state and do not reset.

- `remote/bootswap.sh:59-65`: post-boot recovery staging is unchecked after p4 has already changed. Full/RO FAT, failed `cp`, or failed checksum write still leads to reset without auto-revert. Stage to temporary files, verify and atomically rename them, then reset only on success; otherwise restore and verify stock. Capture hardreset’s return code and exit nonzero instead of returning the final `sync` status.

## MAJOR

- `remote/hardreset.sh:27-40`, `remote/watcher.sh:93`: the five-second loop only stops waiting; it does not terminate/reap `mount`. That child inherits fd 8 and, from watcher, fd 9, potentially pinning both locks forever. It can also complete late and remount RO after fallback remounted RW. Foreground `sync` and fallback remount-RW remain unbounded. Close fd 9 at hardreset entry, close fd 8 in the mount helper, supervise/reap it, bound all filesystem operations, and never issue RW while an RO helper remains unresolved.

- `remote/hardreset.sh:26-31`: only kernel writers are locked; FAT writers are not stopped as required by `PLAN-production-firmware.md:150-157`. Background boot jobs, confirmation tasks, and watcher logging can race the final sync/remount, receive unexpected `EROFS`, or dirty FAT after a failed remount. Add a reset/FAT-writer lock obeyed by every worker and move operational logs/state to ext4 or tmpfs before remounting.

- `remote/watcher.sh:44-46`: timeout kills only the job shell. Descendants survive, continue FAT/device I/O, and retain inherited descriptors. Implement recursive descendant termination through `/proc` with TERM/grace/KILL and waiting; exempt p4 writes from this generic timeout or perform verified recovery while their lock remains held.

- `remote/watcher.sh:16-18,41-49`, `remote/hardreset.sh:18`, `remote/postswap-check.sh:10,16`: logs are written directly to the nearly-full FAT. Job output is unlimited until completion; watcher, hardreset, and postswap logs lack an enforced total cap. Write to `/data` or `/tmp`, publish only capped tails, enforce the 256-KiB aggregate budget, and check every FAT write.

- `remote/postswap-check.sh:23-25,40`: missing metadata exits successfully after the watcher consumed the check, while `dmesg` may already have been cleared by KD. Keep/re-arm recovery on missing metadata and capture `/dev/kmsg` from before validation.

## MINOR

- `remote/jobs/kernel-inventory.sh:7-20`: hash/copy/add failures are ignored, shared temporary names race, and the script may exit zero with incomplete inventory. Lock execution, use per-run temporary files plus atomic renames/fsync, verify copied hashes, and aggregate a nonzero status.

The remaining `flock -n FD`, fractional sleeps, and trap-based detach are compatible with the stated BusyBox/ash constraints.