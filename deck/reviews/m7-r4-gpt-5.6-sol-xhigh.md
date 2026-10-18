- **BLOCKER — `deck/firmware/release/share/gadget.sh:192,266`**: Setup only briefly checks the rollback guard. A rollback can claim it immediately afterward and run `cleanup()` concurrently with configfs setup; conversely, rollback can return success against stock just before setup creates composite. Use one transition mutex across the entire setup/rollback operation, closing its FD in spawned daemons.

- **BLOCKER — `deck/firmware/release/share/gadget.sh:153-171`, `deck/firmware/release/share/flapmon.sh:48`**: The 150-second lease is unsafe and not a watchdog. Legitimate cleanup can exceed 150 seconds, so another caller can SIGKILL it and start a second rollback while orphaned children continue. With only flapmon calling during `--boot`, a hung rollback is never timed out. Use an owner-local bounded supervisor that terminates and reaps all tracked children before releasing ownership; never kill another owner by PID/start-time probing.

- **MAJOR — `deck/firmware/release/share/gadget.sh:150,165,171`**: Lock acquisition and guard publication are fail-open. Failed `mkdir`, FD open, `flock`, or owner-file write still proceeds into unguarded cleanup. Check every operation and abort before mutation on failure.

- **MAJOR — `deck/tools/tf-restore-boot.sh:57-87`**: The “MK20 GPT” check accepts an invalid/non-MK20 partition map: arbitrary GPT revision, arbitrary protective MBR, and arbitrary partition type GUIDs. The passing test even uses fake `b"T"*16` types (`deck/tools/test_tf_gpt.py:22`). Validate the exact protective MBR, GPT 1.0 geometry, and known per-slot type GUIDs before writing.

- **MINOR — `deck/tools/mk20_accept.py:9-12`**: The module documentation still claims HID reports are written and consumed, although the probe is now enumeration-only. Update the check description.

No additional active-connect or unbound-network regression is apparent in the accept/soak diff beyond the explicitly accepted `--tcp` race.
