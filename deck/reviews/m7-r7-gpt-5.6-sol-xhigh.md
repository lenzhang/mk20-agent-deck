Round 7: not ready.

BLOCKER
- `firmware/release/share/gadget.sh:178-187` — Known root-PID-only bug confirmed. A killable supervisor with an unkillable descendant returns `bw=1`; that descendant can retain fd 7 indefinitely. Record PID+start-time for the complete stopped tree and verify every member disappeared; any survivor must return 2 and escalate.
- `firmware/release/share/gadget.sh:191-194` — `escalate()` performs potentially blocking `touch`/`sync` while holding fd 7, and `sync` masks failed touches. Clear busy/close fd 7 first, run marker writes plus sync under a separate bounded supervisor, verify both files, and return a distinct failure status.
- `firmware/release/share/gadget.sh:214` — Known direct/manual rollback gap confirmed: non-stock returns 1 without escalation. Centralize retry-once and escalation inside the public rollback operation, or escalate here before returning.
- `firmware/release/share/gadget.sh:259` — Generation publication is unchecked. A failed write leaves the old/missing token, causing the current flapmon/waiter to receive rc 5 and abandon rollback. Require successful atomic write/readback before mutation; missing/corrupt current generation must not be treated as stale.
- `firmware/release/share/gadget.sh:61-67`, `firmware/release/sbin/mk20d:173-179` — Queued/manual and mk20d selftest rollbacks carry no generation, so they can tear down a newer composite. Assign mk20d’s setup token before invocation, capture the token when queuing rollback, pass it to every delayed rollback, and treat rc 5 as terminal success/no-op.
- `firmware/release/share/gadget.sh:199-202`, `firmware/release/share/flapmon.sh:55-64`, `firmware/release/share/gadget.sh:355` — Generation is checked only after acquiring the mutex. A stale request that times out with rc 4 retries and eventually escalates, rebooting/disabling a newer composite. Check generation before locking, after lock failure, and again after acquisition.
- `firmware/release/sbin/mk20d:173-180` — Recovery is gated on `composite_up`. A partial teardown with ECM already removed but stock still broken skips rollback and escalation entirely. Gate on an explicit `stock_ok` predicate; invoke rollback whenever stock is not verified.

MAJOR
- `firmware/release/share/flapmon.sh:52-56`, `firmware/release/share/gadget.sh:209,244,250` — Busy-marker ownership is racy and rc-3 exits still leave markers. `test; touch` is not atomic, and ownership is forgotten between retries, e.g. rc4→rc5 leaves flapmon’s marker forever. Use per-owner marker files/a busy directory and remove each owner’s marker on every terminal path.

MINOR
- `firmware/release/share/gadget.sh:142-144` still documents the old 240/300-second defaults.

Verified: the fd-7 closures for both KD service actions, fallback KD, bridge, flapmon/waiter, and adbd paths are correct. The double forks reparent successfully for PID-tree killing, but do not create a new session/process group. The nominal 330/450/900-second defaults are internally consistent; the unbounded escalation and descendant bug invalidate the hard guarantee.
