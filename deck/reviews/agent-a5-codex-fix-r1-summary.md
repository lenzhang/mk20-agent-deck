Completed r1. Changed only these files under `deck/agent/`:

- `src/loop.c`, `src/loop.h`: added ERROR allow handling; terminal guards keep aborting while idle/disabled servicing returns zero.
- `src/kbdc.c`: ignores delayed `released` notifications when no token is held.
- `src/loop_test.c`, `tests/test_loop.py`, `tests/test_a5.c`: added resubmission, emergency refusal/reallow, repeated guard, and no-action regressions; fixed the socket acting test’s confirmation policy.
- [NOTES-agent-a5.md](/Users/dev/worktrees/mk20-agent-a1/deck/agent/NOTES-agent-a5.md): added fix r1 details and results.

Validation:

- `make test`: passed with ASan/UBSan.
- Loop tests: **84 passed**, six listener cases deselected.
- Broader socketpair tests: **301 passed**, one existing skip, nine deselected.
- Full pytest: **128 passed, 190 sandbox failures**—184 TCP and six Unix listener EPERM failures, listed separately in the notes.

System `python3` lacks pytest; validation used cached Python/pytest. No commit or device access.