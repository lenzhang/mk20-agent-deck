Verdict: not release-ready.

BLOCKER
- `tools/tf-restore-boot.sh:40-47` — any undersized regular file, including `p4.img`, passes `--whole-image` and is written at LBA0, destroying the partition table. Require `WBYTES == SIZE`, a supplied/sidecar expected SHA, and preferably validate the MK20 partition table.

MAJOR
- `firmware/release/share/gadget.sh:138-139`, `flapmon.sh:41-44` — successful `--rollback-now` always exits 1 because of `exit 1` plus the EXIT trap. Real failure is therefore indistinguishable, and `rollback.once` permanently blocks retries. Run cleanup directly, verify stock ACM/KD/UDC state, return 0 only on success, and release/retry the guard on verified failure.
- `flapmon.sh:16-17`, `gadget.sh:236-240` — a second monitor may wait five seconds for the lock, so `kill -0` after one second falsely reports it started; it can later exit, leaving no monitor. Add an acquired-lock readiness marker and wait for it.
- `tools/mk20_accept.py:70-74,242-250` — client detection fails open: `None` and zero both enter active probes; moreover the pipeline can report zero when `netstat` fails. Distinguish `None`, propagate `netstat` failure, and never connect unless the result is definitively zero.
- `tools/mk20_accept.py:163-167`, `tools/mk20_soak.py:73-76` — `getInfo` still uses an unbound pyserial socket; acceptance continues even after a bad route, and soak performs no route check. Use a protocol client over `TcpTransport(bound_if=iface)`.

MINOR
- `tools/mk20_accept.py:185-193` — the shared `hidtap.done` lets an older detached writer satisfy a later probe and repeated pending probes accumulate writers. Use a unique marker plus an atomic in-progress lock.
- `tools/mk20_soak.py:105-109` — no single-instance lock; two starts within one minute share data/summary paths and overwrite `soak.pid`. Lock the output directory and include seconds/PID in filenames.

Suspend classification, supported BusyBox `flock -n FD`, stopfile recheck, all-released HID payload, and host `killpg` handling otherwise look sound.
