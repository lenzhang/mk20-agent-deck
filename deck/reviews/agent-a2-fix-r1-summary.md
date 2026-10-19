Implemented both fixes under `deck/agent/`, left uncommitted.

- Added deadline checks per tile/row and around finalization/JPEG encoding; expiry returns `timeout`.
- Linux RSS now reads `/proc/self/status` `VmHWM`, with `getrusage` fallback. macOS normalization remains.
- Added compressed-frame timeout coverage and strengthened RSS tests with a 64 MiB pre-exec launcher. Updated [NOTES-agent-a2.md](/Users/dev/worktrees/mk20-agent-a1/deck/agent/NOTES-agent-a2.md).

Validation:

- `make -C deck/agent test`: passed with ASan/UBSan.
- Socketpair pytest run: **124 passed, 1 HTTP test skipped**.
- Full agent suite: **38 passed, 94 sandbox-blocked failures** at loopback `bind()` (`EPERM`).
- Linux branch passed C11 warning checks; Linux runtime validation remains pending.