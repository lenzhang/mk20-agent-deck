- **BLOCKER — `deck/firmware/release/share/gadget.sh:160,176-185`**: `bounded_wait` checks only the root PID. `kill_tree` can kill/reap that shell while an unkillable descendant survives holding inherited fd 7, so it returns `1` instead of `2` and never escalates. Snapshot every descendant’s PID/start-time before killing, then poll all snapshots; return `2` if any survives. Add a test where the root dies but a grandchild remains.

- **BLOCKER — `gadget.sh:194-206`**: a rollback that finishes but cannot reach `stock_ok` merely returns 1. Direct queued/manual rollback has no retry/escalation wrapper. Invoke the escalation path after the settle deadline, or make every entry point use a bounded retry-and-escalate wrapper.

- **BLOCKER — `gadget.sh:194,224-250,346`**: an old flapmon/confirm rollback can already be waiting for fd 7 when a newer setup wins the lock. After setup succeeds, that stale request acquires the lock and tears down the new composite. Add a transition generation token, captured by each monitor/waiter and revalidated after acquiring fd 7; treat mismatch as cancellation, not success.

- **MAJOR — `gadget.sh:189-192`**: `touch flag && touch reboot; sync` masks touch failures with `sync`’s status, while callers unconditionally return rc 3 (“self-escalated”). It can also hang under the mutex. Run escalation in a bounded child with fd 7 closed, verify both files, and emit rc 3 only after confirmed success.

- **MINOR — `gadget.sh:201-202,242-243`**: unkillable-child branches exit before clearing `gadget.busy`. Move removal before every rc-3 early exit.

The knob validation, explicit extraction markers, flapmon rc-3 no-retry, and confirm waiter’s single retry are present.
